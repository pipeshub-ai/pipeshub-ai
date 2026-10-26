"""The rules for putting a rate-limited record back in line, and for ending its wait."""
from __future__ import annotations

import pytest
from tenacity import AsyncRetrying, RetryError, stop_after_attempt

from app.modules.indexing.rate_limit_deferral import (
    RATE_LIMITED_SINCE_FIELD,
    RETRY_NOT_BEFORE_FIELD,
    deferral_due,
    deferral_updates,
    is_deferred,
    plan_deferral,
    rate_limit_retry_after,
)
from app.utils.api_call import ApiCallError

HOUR_MS = 3600 * 1000
DAY_S = 24 * 3600.0
NOW = 1_800_000_000_000


def _limited(retry_after: float | None = 1800, status: int = 429) -> ApiCallError:
    return ApiCallError("Too many requests", status_code=status, retry_after_seconds=retry_after)


class TestRetryAfter:
    def test_a_429_that_says_when(self) -> None:
        assert rate_limit_retry_after(_limited(1800)) == 1800

    def test_a_429_that_does_not_say_is_not_deferred(self) -> None:
        assert rate_limit_retry_after(_limited(None)) is None

    def test_other_statuses_are_not_deferred(self) -> None:
        assert rate_limit_retry_after(_limited(1800, status=503)) is None
        assert rate_limit_retry_after(ValueError("boom")) is None

    def test_found_under_a_wrapper(self) -> None:
        try:
            try:
                raise _limited(600)
            except ApiCallError as inner:
                raise RuntimeError("download failed") from inner
        except RuntimeError as outer:
            assert rate_limit_retry_after(outer) == 600

    async def test_found_inside_tenacitys_retry_error(self) -> None:
        with pytest.raises(RetryError) as exc_info:
            async for attempt in AsyncRetrying(stop=stop_after_attempt(2)):
                with attempt:
                    raise _limited(10)
        assert rate_limit_retry_after(exc_info.value) == 10


class TestPlan:
    def test_a_first_wait_starts_now(self) -> None:
        plan = plan_deferral({}, 1800, now_ms=NOW, max_total_seconds=DAY_S)
        assert plan.not_before_ms == NOW + 1800 * 1000
        assert plan.since_ms == NOW
        assert plan.give_up is False

    def test_a_zero_retry_after_still_lands_after_now(self) -> None:
        plan = plan_deferral({}, 0, now_ms=NOW, max_total_seconds=DAY_S)
        assert plan.not_before_ms > NOW

    def test_a_wait_that_follows_one_carries_its_start(self) -> None:
        record = {RATE_LIMITED_SINCE_FIELD: NOW - 5 * HOUR_MS, RETRY_NOT_BEFORE_FIELD: NOW - 60_000}
        plan = plan_deferral(record, 1800, now_ms=NOW, max_total_seconds=DAY_S)
        assert plan.since_ms == NOW - 5 * HOUR_MS
        assert plan.give_up is False

    def test_a_wait_that_would_end_past_the_allowance_gives_up(self) -> None:
        record = {RATE_LIMITED_SINCE_FIELD: NOW - 23 * HOUR_MS - 50 * 60_000, RETRY_NOT_BEFORE_FIELD: NOW - 60_000}
        plan = plan_deferral(record, 1800, now_ms=NOW, max_total_seconds=DAY_S)
        assert plan.give_up is True

    def test_one_retry_after_longer_than_the_allowance_gives_up_at_once(self) -> None:
        plan = plan_deferral({}, DAY_S + 60, now_ms=NOW, max_total_seconds=DAY_S)
        assert plan.give_up is True

    def test_a_new_run_starts_now_for_a_record_waiting_its_turn(self) -> None:
        record = {RATE_LIMITED_SINCE_FIELD: NOW - 23 * HOUR_MS - 50 * 60_000, RETRY_NOT_BEFORE_FIELD: NOW - 60_000}
        plan = plan_deferral(record, 1800, now_ms=NOW, max_total_seconds=DAY_S, new_run=True)
        assert plan.since_ms == NOW
        assert plan.not_before_ms == NOW + 1800 * 1000
        assert plan.give_up is False

    def test_a_new_run_still_gives_up_on_one_wait_longer_than_the_allowance(self) -> None:
        record = {RATE_LIMITED_SINCE_FIELD: NOW - 5 * HOUR_MS, RETRY_NOT_BEFORE_FIELD: NOW - 60_000}
        plan = plan_deferral(record, DAY_S + 60, now_ms=NOW, max_total_seconds=DAY_S, new_run=True)
        assert plan.give_up is True

    def test_a_wait_left_alone_longer_than_the_allowance_starts_over(self) -> None:
        record = {RATE_LIMITED_SINCE_FIELD: NOW - 30 * 24 * HOUR_MS, RETRY_NOT_BEFORE_FIELD: NOW - 25 * HOUR_MS}
        plan = plan_deferral(record, 1800, now_ms=NOW, max_total_seconds=DAY_S)
        assert plan.since_ms == NOW
        assert plan.give_up is False

    def test_the_record_is_queued_with_nothing_claiming_it(self) -> None:
        plan = plan_deferral({}, 1800, now_ms=NOW, max_total_seconds=DAY_S)
        updates = deferral_updates(
            plan, reason="paused", record={"parsingStatus": "IN_PROGRESS"}, now_ms=NOW
        )
        assert updates == {
            "indexingStatus": "QUEUED",
            "queuedAtTimestamp": NOW,
            "processingStartedAt": None,
            "reason": "paused",
            "parsingStatus": "NOT_STARTED",
            RETRY_NOT_BEFORE_FIELD: NOW + 1800 * 1000,
            RATE_LIMITED_SINCE_FIELD: NOW,
        }
        assert is_deferred(updates)
        assert not is_deferred({})


class TestDue:
    @staticmethod
    def _deferred(**fields: object) -> dict:
        return {"queuedAtTimestamp": NOW, RETRY_NOT_BEFORE_FIELD: NOW + HOUR_MS, **fields}

    def test_not_a_deferred_record(self) -> None:
        assert deferral_due({"queuedAtTimestamp": NOW}, NOW) is None

    def test_not_before_its_time(self) -> None:
        assert deferral_due(self._deferred(), NOW + HOUR_MS - 1) is False

    def test_due_once_its_time_has_come(self) -> None:
        assert deferral_due(self._deferred(), NOW + HOUR_MS) is True

    def test_not_again_once_the_sweep_has_sent_it(self) -> None:
        assert deferral_due(self._deferred(lastRepublishedAt=NOW + HOUR_MS + 5), NOW + 2 * HOUR_MS) is None

    def test_an_earlier_send_does_not_count(self) -> None:
        assert deferral_due(self._deferred(lastRepublishedAt=NOW - HOUR_MS), NOW + HOUR_MS) is True

    def test_queued_again_after_its_time_by_something_else(self) -> None:
        assert deferral_due(self._deferred(queuedAtTimestamp=NOW + 2 * HOUR_MS), NOW + 3 * HOUR_MS) is None

    def test_a_malformed_value_is_not_a_wait(self) -> None:
        assert deferral_due({RETRY_NOT_BEFORE_FIELD: "soon"}, NOW) is None
