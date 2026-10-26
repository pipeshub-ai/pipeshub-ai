"""CrawlBudget, run_limits and RunReport (scenarios BU-01 to BU-04)."""

import asyncio
import json

import pytest

from app.connectors.sources.web.budget import (
    MAX_RUN_HOURS_ENV,
    STALL_MINUTES_ENV,
    CrawlBudget,
    RunOutcome,
    RunReport,
    StopReason,
    run_limits,
)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _scheduled(minutes: object) -> dict[str, object]:
    return {"selectedStrategy": "SCHEDULED", "scheduledConfig": {"intervalMinutes": minutes}}


class TestRunLimits:
    def test_a_manual_crawl_gets_a_day_and_an_hour_of_stall(self) -> None:
        assert run_limits({"selectedStrategy": "MANUAL"}, {}) == (24 * 3600, 3600)

    def test_a_daily_schedule_leaves_room_before_the_next_run(self) -> None:
        deadline, _ = run_limits(_scheduled(1440), {})
        assert deadline == pytest.approx(0.9 * 24 * 3600)

    def test_an_hourly_schedule(self) -> None:
        assert run_limits(_scheduled(60), {})[0] == pytest.approx(54 * 60)

    @pytest.mark.parametrize("bad", [None, 0, -5, "soon", {}])
    def test_a_broken_interval_falls_back_to_a_day(self, bad: object) -> None:
        assert run_limits(_scheduled(bad), {})[0] == 24 * 3600

    def test_the_strategy_name_is_matched_case_blind(self) -> None:
        assert run_limits({**_scheduled(120), "selectedStrategy": "scheduled"}, {})[0] == pytest.approx(108 * 60)

    def test_operator_env_settings_win(self) -> None:
        env = {MAX_RUN_HOURS_ENV: "2", STALL_MINUTES_ENV: "5"}
        assert run_limits(_scheduled(1440), env) == (7200, 300)

    @pytest.mark.parametrize("bad", ["", "0", "-1", "x"])
    def test_invalid_env_settings_are_ignored(self, bad: str) -> None:
        assert run_limits({}, {MAX_RUN_HOURS_ENV: bad, STALL_MINUTES_ENV: bad}) == (24 * 3600, 3600)


class TestCrawlBudget:
    def test_nothing_to_stop_for_at_the_start(self) -> None:
        assert CrawlBudget(now=Clock(), deadline_seconds=60, stall_seconds=30).check() is None

    def test_the_deadline_stops_a_crawl_that_keeps_making_progress(self) -> None:
        clock = Clock()
        budget = CrawlBudget(now=clock, deadline_seconds=60, stall_seconds=30)
        for _ in range(6):
            clock.t += 10
            budget.progress()
        assert budget.check() is StopReason.TIME_LIMIT

    def test_no_progress_for_the_stall_window_stops_the_crawl(self) -> None:
        clock = Clock()
        budget = CrawlBudget(now=clock, deadline_seconds=600, stall_seconds=30)
        clock.t += 29
        assert budget.check() is None
        clock.t += 1
        assert budget.check() is StopReason.STALLED

    def test_progress_resets_the_stall_window(self) -> None:
        clock = Clock()
        budget = CrawlBudget(now=clock, deadline_seconds=600, stall_seconds=30)
        clock.t += 25
        budget.progress()
        clock.t += 25
        assert budget.check() is None

    def test_the_reason_sticks_once_set(self) -> None:
        clock = Clock()
        budget = CrawlBudget(now=clock, deadline_seconds=10, stall_seconds=5)
        clock.t += 6
        assert budget.check() is StopReason.STALLED
        clock.t += 10
        budget.progress()
        assert budget.check() is StopReason.STALLED

    @pytest.mark.parametrize("deadline, stall", [(0, 1), (1, 0), (-1, 1)])
    def test_limits_must_be_positive(self, deadline: float, stall: float) -> None:
        with pytest.raises(ValueError):
            CrawlBudget(now=Clock(), deadline_seconds=deadline, stall_seconds=stall)

    async def test_watch_cancels_a_task_that_never_returns(self) -> None:
        loop = asyncio.get_running_loop()
        budget = CrawlBudget(now=loop.time, deadline_seconds=60, stall_seconds=0.05)
        hung = asyncio.ensure_future(asyncio.Event().wait())

        await asyncio.wait_for(budget.watch(hung, interval=0.01), 2)

        with pytest.raises(asyncio.CancelledError):
            await hung
        assert budget.stop_reason is StopReason.STALLED

    async def test_watch_returns_when_the_task_finishes_first(self) -> None:
        loop = asyncio.get_running_loop()
        budget = CrawlBudget(now=loop.time, deadline_seconds=60, stall_seconds=60)
        quick = asyncio.ensure_future(asyncio.sleep(0))

        await asyncio.wait_for(budget.watch(quick, interval=0.01), 2)

        assert budget.stop_reason is None


def test_a_report_is_stored_as_plain_json() -> None:
    report = RunReport(started_at=1, outcome=RunOutcome.PARTIAL, stop_reason=StopReason.TIME_LIMIT)
    report.failed_by_status.update(["404", "404", "no_answer"])

    data = json.loads(report.to_json())

    assert data["outcome"] == "partial"
    assert data["stop_reason"] == "time_limit"
    assert data["failed_by_status"] == {"404": 2, "no_answer": 1}
