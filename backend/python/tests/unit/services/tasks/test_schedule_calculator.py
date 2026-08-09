"""Unit tests for `ScheduleCalculator` -- pure schedule math, DST correctness.

`America/New_York` transitions used for 2024: spring-forward 2024-03-10
02:00 -> 03:00 (clocks skip 2am-3am, EST->EDT); fall-back 2024-11-03 02:00
-> 01:00 (clocks repeat 1am-2am, EDT->EST).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.services.tasks.domain.errors import InvalidScheduleError
from app.services.tasks.domain.models import TaskTrigger, TriggerKind
from app.services.tasks.domain.policies import MisfirePolicy
from app.services.tasks.domain.schedule_calculator import (
    ScheduleCalculator,
    validate_cron_expression,
    validate_timezone,
)


@pytest.fixture
def calc() -> ScheduleCalculator:
    return ScheduleCalculator()


class TestCronValidation:
    def test_valid_expression(self) -> None:
        validate_cron_expression("0 9 * * *")  # no raise

    def test_invalid_expression(self) -> None:
        with pytest.raises(InvalidScheduleError):
            validate_cron_expression("not a cron")

    def test_invalid_timezone(self) -> None:
        with pytest.raises(InvalidScheduleError):
            validate_timezone("Mars/Phobos")

    def test_valid_timezone(self) -> None:
        validate_timezone("America/New_York")  # no raise


class TestCronDST:
    def test_spring_forward(self, calc: ScheduleCalculator) -> None:
        """9am daily America/New_York across the spring-forward boundary
        must fire at 9am local time on both sides -- 14:00 UTC (EST, -5)
        before, 13:00 UTC (EDT, -4) after."""
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.CRON,
            cron_expression="0 9 * * *", timezone="America/New_York",
        )
        before = datetime(2024, 3, 9, 12, 0, tzinfo=timezone.utc)
        first = calc.next_run_at(trig, after=before)
        assert first == "2024-03-09T14:00:00+00:00"

        second = calc.next_run_at(trig, after=datetime.fromisoformat(first))
        assert second == "2024-03-10T13:00:00+00:00"

    def test_fall_back(self, calc: ScheduleCalculator) -> None:
        """9am daily across fall-back: EDT (-4) before, EST (-5) after."""
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.CRON,
            cron_expression="0 9 * * *", timezone="America/New_York",
        )
        before = datetime(2024, 11, 2, 12, 0, tzinfo=timezone.utc)
        first = calc.next_run_at(trig, after=before)
        assert first == "2024-11-02T13:00:00+00:00"

        second = calc.next_run_at(trig, after=datetime.fromisoformat(first))
        assert second == "2024-11-03T14:00:00+00:00"

    def test_missing_cron_expression_raises(self) -> None:
        """`TaskTrigger`'s own kind-specific validator (Phase 8) now rejects
        this at construction time, before `ScheduleCalculator` ever sees
        it -- `_next_cron`'s own `InvalidScheduleError` check is unreachable
        defense-in-depth, kept in case a future relaxation of the model
        validator reopens the gap."""
        with pytest.raises(ValidationError, match="cron_expression"):
            TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.CRON)


class TestOneTime:
    def test_future_fire_at(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.ONE_TIME,
            fire_at="2099-01-01T00:00:00+00:00",
        )
        nxt = calc.next_run_at(trig, after=datetime(2024, 1, 1, tzinfo=timezone.utc))
        assert nxt == "2099-01-01T00:00:00+00:00"

    def test_already_run_returns_none(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.ONE_TIME,
            fire_at="2020-01-01T00:00:00+00:00", run_count=1,
        )
        assert calc.next_run_at(trig) is None

    def test_past_fire_at_skip_policy_returns_none(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.ONE_TIME,
            fire_at="2020-01-01T00:00:00+00:00", misfire_policy=MisfirePolicy.SKIP,
        )
        assert calc.next_run_at(trig, after=datetime(2024, 1, 1, tzinfo=timezone.utc)) is None

    def test_missing_fire_at_raises(self) -> None:
        """Rejected at construction time -- see `test_missing_cron_expression_raises`."""
        with pytest.raises(ValidationError, match="fire_at"):
            TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.ONE_TIME)


class TestInterval:
    def test_basic(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.INTERVAL, interval_seconds=3600)
        reference = datetime(2024, 1, 1, tzinfo=timezone.utc)
        nxt = calc.next_run_at(trig, after=reference)
        assert nxt == "2024-01-01T01:00:00+00:00"

    def test_from_last_fire(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.INTERVAL, interval_seconds=1800,
            last_fire_at="2024-01-01T00:00:00+00:00",
        )
        nxt = calc.next_run_at(trig, after=datetime(2024, 1, 1, 0, 5, tzinfo=timezone.utc))
        assert nxt == "2024-01-01T00:30:00+00:00"

    def test_catch_up_after_long_gap(self, calc: ScheduleCalculator) -> None:
        """If the scheduler was down for a long time, next_run_at must jump
        to the next boundary at/after `reference`, not return a time still
        in the past."""
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.INTERVAL, interval_seconds=60,
            last_fire_at="2024-01-01T00:00:00+00:00",
        )
        reference = datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)  # 1 hour later
        nxt = calc.next_run_at(trig, after=reference)
        nxt_dt = datetime.fromisoformat(nxt)
        assert nxt_dt > reference

    def test_zero_interval_raises(self) -> None:
        """Rejected at construction time -- see `test_missing_cron_expression_raises`."""
        with pytest.raises(ValidationError, match="interval_seconds"):
            TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.INTERVAL, interval_seconds=0)


class TestDisabledAndExhausted:
    def test_disabled_returns_none(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.CRON,
            cron_expression="0 9 * * *", enabled=False,
        )
        assert calc.next_run_at(trig) is None

    def test_max_runs_reached_returns_none(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.CRON,
            cron_expression="0 9 * * *", max_runs=1, run_count=1,
        )
        assert calc.next_run_at(trig) is None

    def test_event_kind_returns_none(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.EVENT,
            event_filter={"event_type": "record.created"},
        )
        assert calc.next_run_at(trig) is None

    def test_webhook_kind_returns_none(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.WEBHOOK, webhook_id="wh-1")
        assert calc.next_run_at(trig) is None


class TestMisfirePolicy:
    def test_skip_fires_once_now(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.CRON,
            cron_expression="* * * * *", misfire_policy=MisfirePolicy.SKIP,
            next_run_at="2024-01-01T00:00:00+00:00",
        )
        claimed_at = datetime(2024, 1, 1, 5, 0, tzinfo=timezone.utc)
        fires = calc.apply_misfire_policy(trig, claimed_at=claimed_at)
        assert fires == [claimed_at.isoformat()]

    def test_run_once_fires_at_due_time(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.CRON,
            cron_expression="* * * * *", misfire_policy=MisfirePolicy.RUN_ONCE,
            next_run_at="2024-01-01T00:00:00+00:00",
        )
        claimed_at = datetime(2024, 1, 1, 5, 0, tzinfo=timezone.utc)
        fires = calc.apply_misfire_policy(trig, claimed_at=claimed_at)
        assert fires == ["2024-01-01T00:00:00+00:00"]

    def test_run_all_replays_every_missed_tick(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.INTERVAL, interval_seconds=3600,
            misfire_policy=MisfirePolicy.RUN_ALL,
            next_run_at="2024-01-01T00:00:00+00:00",
        )
        claimed_at = datetime(2024, 1, 1, 3, 0, tzinfo=timezone.utc)
        fires = calc.apply_misfire_policy(trig, claimed_at=claimed_at)
        assert fires == [
            "2024-01-01T00:00:00+00:00",
            "2024-01-01T01:00:00+00:00",
            "2024-01-01T02:00:00+00:00",
            "2024-01-01T03:00:00+00:00",
        ]

    def test_no_next_run_at_returns_empty(self, calc: ScheduleCalculator) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.EVENT,
            event_filter={"event_type": "record.created"}, next_run_at=None,
        )
        assert calc.apply_misfire_policy(trig, claimed_at=datetime.now(timezone.utc)) == []

    def test_run_all_capped(self, calc: ScheduleCalculator) -> None:
        """A pathological backlog (per-minute cron down for a very long
        time) must not generate an unbounded list."""
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.INTERVAL, interval_seconds=1,
            misfire_policy=MisfirePolicy.RUN_ALL,
            next_run_at="2024-01-01T00:00:00+00:00",
        )
        claimed_at = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc) + timedelta(days=1)
        fires = calc.apply_misfire_policy(trig, claimed_at=claimed_at)
        assert len(fires) <= 500
