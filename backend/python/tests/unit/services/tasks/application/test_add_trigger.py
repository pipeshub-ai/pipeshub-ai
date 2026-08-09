"""`add_trigger` is what declarative `@workflow(triggers=[...])` declarations
go through. The bug it exists to prevent: a trigger row stored with
`next_run_at=None` is never picked up by the scheduler's due-index scan, so
the workflow silently never fires -- the failure looks identical to "nothing
was scheduled" from the user's side.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.tasks.application.engine import TaskEngine
from app.services.tasks.domain.errors import InvalidTriggerError, TaskNotFoundError
from app.services.tasks.domain.models import (
    TaskDefinition,
    TaskPrincipal,
    TaskStatus,
    TriggerKind,
    compute_declarative_trigger_id,
)
from app.services.tasks.interface.clock import FixedClock

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class _FakeTaskStore:
    def __init__(self, task: TaskDefinition | None) -> None:
        self._task = task

    async def get(self, task_id: str, org_id: str) -> TaskDefinition | None:  # noqa: ARG002
        return self._task


class _FakeTriggerStore:
    def __init__(self) -> None:
        self.rows: dict[str, object] = {}

    async def upsert(self, trigger):
        self.rows[trigger.trigger_id] = trigger
        return trigger

    async def list_for_task(self, task_id: str) -> list:  # noqa: ARG002
        return list(self.rows.values())

    async def list_for_tasks(self, task_ids) -> dict:  # noqa: ANN001, ARG002
        return {}


class _NullProducer:
    async def send_event(self, *args, **kwargs) -> bool:  # noqa: ANN002, ANN003, ARG002
        return True


def _task() -> TaskDefinition:
    return TaskDefinition(
        task_id="wf-1",
        org_id="org-1",
        created_by_user_id="u-1",
        principal=TaskPrincipal(org_id="org-1", user_id="u-1", user_email="u@example.com"),
        title="Daily digest",
        description="post the digest every morning",
        instructions="post the digest",
        status=TaskStatus.ACTIVE,
    )


_UNSET = object()


def _engine(task: object = _UNSET) -> tuple[TaskEngine, _FakeTriggerStore]:
    triggers = _FakeTriggerStore()
    engine = TaskEngine(
        task_store=_FakeTaskStore(_task() if task is _UNSET else task),
        trigger_store=triggers,
        run_store=object(),
        producer=_NullProducer(),
        clock=FixedClock(_NOW),
    )
    return engine, triggers


class TestScheduledTriggers:
    @pytest.mark.asyncio
    async def test_cron_trigger_is_stored_with_a_future_next_run_at(self) -> None:
        engine, triggers = _engine()

        trigger, secret = await engine.add_trigger(
            "wf-1", "org-1", {"kind": "cron", "cron_expression": "0 9 * * *"},
        )

        assert secret is None
        assert trigger.kind is TriggerKind.CRON
        stored = triggers.rows[trigger.trigger_id]
        assert stored.next_run_at is not None
        assert datetime.fromisoformat(stored.next_run_at) > _NOW

    @pytest.mark.asyncio
    async def test_interval_trigger_is_stored_with_a_future_next_run_at(self) -> None:
        engine, triggers = _engine()

        trigger, _ = await engine.add_trigger(
            "wf-1", "org-1", {"kind": "interval", "interval_seconds": 3600},
        )

        stored = triggers.rows[trigger.trigger_id]
        assert datetime.fromisoformat(stored.next_run_at) == _NOW + timedelta(seconds=3600)

    @pytest.mark.asyncio
    async def test_a_past_one_time_fire_at_is_rejected_instead_of_stored_dead(self) -> None:
        engine, triggers = _engine()
        past = (_NOW - timedelta(days=1)).isoformat()

        with pytest.raises(InvalidTriggerError, match="future"):
            await engine.add_trigger("wf-1", "org-1", {"kind": "one_time", "fire_at": past})

        assert triggers.rows == {}

    @pytest.mark.asyncio
    async def test_an_unparseable_cron_is_rejected(self) -> None:
        engine, triggers = _engine()

        with pytest.raises(InvalidTriggerError):
            await engine.add_trigger(
                "wf-1", "org-1", {"kind": "cron", "cron_expression": "not a cron"},
            )

        assert triggers.rows == {}


class TestDeclarativeReconciliation:
    @pytest.mark.asyncio
    async def test_regenerating_identical_code_updates_the_same_row(self) -> None:
        """Without a content-derived id, every regeneration inserted a fresh
        trigger and the workflow fired N times a day instead of once."""
        engine, triggers = _engine()
        spec = {"kind": "cron", "cron_expression": "0 9 * * *"}
        trigger_id = compute_declarative_trigger_id("wf-1", spec)

        await engine.add_trigger("wf-1", "org-1", spec, trigger_id=trigger_id)
        await engine.add_trigger("wf-1", "org-1", spec, trigger_id=trigger_id)

        assert list(triggers.rows) == [trigger_id]

    @pytest.mark.asyncio
    async def test_different_specs_get_different_ids(self) -> None:
        engine, triggers = _engine()
        daily = {"kind": "cron", "cron_expression": "0 9 * * *"}
        hourly = {"kind": "cron", "cron_expression": "0 * * * *"}

        for spec in (daily, hourly):
            await engine.add_trigger(
                "wf-1", "org-1", spec,
                trigger_id=compute_declarative_trigger_id("wf-1", spec),
            )

        assert len(triggers.rows) == 2


class TestOwnership:
    @pytest.mark.asyncio
    async def test_cannot_attach_a_trigger_to_another_orgs_workflow(self) -> None:
        engine, triggers = _engine(task=None)

        with pytest.raises(TaskNotFoundError):
            await engine.add_trigger(
                "wf-1", "org-INTRUDER", {"kind": "interval", "interval_seconds": 60},
            )

        assert triggers.rows == {}
