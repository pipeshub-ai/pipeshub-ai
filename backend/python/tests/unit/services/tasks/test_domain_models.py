"""Unit tests for `app.services.tasks.domain.models` -- pure data, no I/O."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.tasks.domain.models import (
    Page,
    RunStatus,
    TaskDefinition,
    TaskPrincipal,
    TaskQuery,
    TaskRun,
    TaskStatus,
    TaskStep,
    TaskTrigger,
    TriggerKind,
    compute_idempotency_key,
)


def _principal() -> TaskPrincipal:
    return TaskPrincipal(org_id="org-1", user_id="user-1", user_email="a@b.com")


class TestTaskDefinition:
    def test_defaults(self) -> None:
        task = TaskDefinition(
            org_id="org-1",
            created_by_user_id="user-1",
            principal=_principal(),
            title="Daily digest",
            description="every morning summarize tickets",
            instructions="Summarize yesterday's tickets and post to #support",
        )
        assert task.revision == 0
        assert task.status == TaskStatus.DRAFT
        assert task.enabled is True
        assert task.schema_version == 2  # bumped when execution_kind + workflow_version_id were added
        assert task.task_id  # uuid4 default, non-empty

    def test_next_revision(self) -> None:
        task = TaskDefinition(
            org_id="org-1", created_by_user_id="user-1", principal=_principal(),
            title="t", description="d", instructions="i", revision=3,
        )
        assert task.next_revision() == 4

    def test_steps_dag_roundtrip(self) -> None:
        steps = [
            TaskStep(id="s1", description="fetch", domain="jira"),
            TaskStep(id="s2", description="summarize", domain="llm", depends_on=["s1"]),
        ]
        task = TaskDefinition(
            org_id="org-1", created_by_user_id="user-1", principal=_principal(),
            title="t", description="d", instructions="i", steps=steps,
        )
        dumped = task.model_dump(mode="json")
        restored = TaskDefinition.model_validate(dumped)
        assert restored.steps is not None
        assert restored.steps[1].depends_on == ["s1"]


class TestTaskTrigger:
    def test_is_exhausted_none_max_runs(self) -> None:
        trig = TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.CRON, cron_expression="0 9 * * *")
        assert trig.is_exhausted() is False

    def test_is_exhausted_true(self) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.ONE_TIME,
            fire_at="2024-01-01T00:00:00+00:00", max_runs=1, run_count=1,
        )
        assert trig.is_exhausted() is True

    def test_is_exhausted_false_under_limit(self) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.INTERVAL,
            interval_seconds=60, max_runs=5, run_count=2,
        )
        assert trig.is_exhausted() is False


class TestTaskTriggerKindSpecificValidation:
    """`_check_kind_specific_fields` -- every construction path (creation,
    `_hash_to_trigger` deserialization, REST bodies) funnels through this
    same validator."""

    def test_cron_without_cron_expression_raises(self) -> None:
        with pytest.raises(ValidationError, match="cron_expression"):
            TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.CRON)

    def test_interval_without_interval_seconds_raises(self) -> None:
        with pytest.raises(ValidationError, match="interval_seconds"):
            TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.INTERVAL)

    def test_interval_with_zero_interval_seconds_raises(self) -> None:
        with pytest.raises(ValidationError, match="interval_seconds"):
            TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.INTERVAL, interval_seconds=0)

    def test_one_time_without_fire_at_raises(self) -> None:
        with pytest.raises(ValidationError, match="fire_at"):
            TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.ONE_TIME)

    def test_event_without_event_filter_raises(self) -> None:
        with pytest.raises(ValidationError, match="event_filter"):
            TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.EVENT)

    def test_event_without_event_type_in_filter_raises(self) -> None:
        with pytest.raises(ValidationError, match="event_filter"):
            TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.EVENT, event_filter={"connectorId": "conn-1"})

    def test_event_with_event_type_is_valid(self) -> None:
        trig = TaskTrigger(
            task_id="t1", org_id="org-1", kind=TriggerKind.EVENT,
            event_filter={"event_type": "record.created"},
        )
        assert trig.event_filter["event_type"] == "record.created"

    def test_webhook_without_webhook_id_raises(self) -> None:
        with pytest.raises(ValidationError, match="webhook_id"):
            TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.WEBHOOK)

    def test_webhook_with_webhook_id_is_valid(self) -> None:
        trig = TaskTrigger(task_id="t1", org_id="org-1", kind=TriggerKind.WEBHOOK, webhook_id="wh-1")
        assert trig.webhook_id == "wh-1"


class TestTaskRun:
    def test_is_terminal(self) -> None:
        for status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.ABANDONED, RunStatus.DLQ, RunStatus.CANCELLED):
            run = TaskRun(task_id="t1", org_id="org-1", idempotency_key="k", status=status)
            assert run.is_terminal() is True

    def test_is_not_terminal(self) -> None:
        for status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.AWAITING_INPUT):
            run = TaskRun(task_id="t1", org_id="org-1", idempotency_key="k", status=status)
            assert run.is_terminal() is False


class TestIdempotencyKey:
    def test_deterministic(self) -> None:
        k1 = compute_idempotency_key("task-1", "2024-01-01T09:00:00+00:00")
        k2 = compute_idempotency_key("task-1", "2024-01-01T09:00:00+00:00")
        assert k1 == k2

    def test_distinguishes_fire_time(self) -> None:
        k1 = compute_idempotency_key("task-1", "2024-01-01T09:00:00+00:00")
        k2 = compute_idempotency_key("task-1", "2024-01-02T09:00:00+00:00")
        assert k1 != k2

    def test_distinguishes_task(self) -> None:
        k1 = compute_idempotency_key("task-1", "2024-01-01T09:00:00+00:00")
        k2 = compute_idempotency_key("task-2", "2024-01-01T09:00:00+00:00")
        assert k1 != k2


class TestPage:
    def test_has_more(self) -> None:
        page: Page[int] = Page(items=[1, 2, 3], total=10, limit=3, offset=0)
        assert page.has_more is True

    def test_no_more(self) -> None:
        page: Page[int] = Page(items=[1, 2, 3], total=3, limit=3, offset=0)
        assert page.has_more is False


class TestTaskQuery:
    def test_defaults(self) -> None:
        q = TaskQuery(org_id="org-1")
        assert q.limit == 50
        assert q.offset == 0
        assert q.status is None
