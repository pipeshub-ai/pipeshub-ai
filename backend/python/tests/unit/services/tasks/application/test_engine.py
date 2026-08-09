"""Unit tests for `TaskEngine` -- the use-case layer shared by
`task_manage`/REST routes. Uses plain in-memory fakes for
`ITaskStore`/`ITriggerStore`/`ITaskRunStore`/`IMessagingProducer` (each
port's own contract is proven separately by
`tests/unit/services/tasks/adapters/test_*_contract.py`), so these tests
exercise `TaskEngine`'s own orchestration logic in isolation: prerequisite
gating, DAG validation, optimistic-concurrency retry, run_now idempotency,
and promote_to_agent delegation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.tasks.application.engine import TaskEngine
from app.services.tasks.application.prerequisites import (
    PrerequisiteValidator,
)
from app.services.tasks.domain.errors import (
    ExpiredSuspensionError,
    InvalidTriggerError,
    OptimisticConcurrencyError,
    PrerequisiteError,
    RunNotFoundError,
    StaleAnswerError,
    TaskDAGError,
    TaskNotFoundError,
    TriggerNotFoundError,
)
from app.services.tasks.domain.models import (
    RunStatus,
    TaskDefinition,
    TaskRun,
    TaskStatus,
    TaskStep,
    TaskTrigger,
    TriggerKind,
)
from app.services.tasks.interface.clock import FixedClock


class FakeTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskDefinition] = {}

    async def create(self, task: TaskDefinition) -> TaskDefinition:
        self._tasks[task.task_id] = task
        return task

    async def get(self, task_id: str, org_id: str) -> TaskDefinition | None:
        task = self._tasks.get(task_id)
        if task is None or task.org_id != org_id:
            return None
        return task

    async def update(self, task: TaskDefinition, *, expected_revision: int) -> TaskDefinition:
        current = self._tasks.get(task.task_id)
        actual = current.revision if current is not None else -1
        if current is None or actual != expected_revision:
            raise OptimisticConcurrencyError(task.task_id, expected_revision, actual)
        updated = task.model_copy(update={"revision": expected_revision + 1})
        self._tasks[task.task_id] = updated
        return updated

    async def delete(self, task_id: str, org_id: str) -> bool:
        task = await self.get(task_id, org_id)
        if task is None:
            return False
        del self._tasks[task_id]
        return True

    async def list(self, query: object) -> None:  # pragma: no cover - unused
        raise NotImplementedError


class FakeTriggerStore:
    def __init__(self) -> None:
        self._triggers: dict[str, TaskTrigger] = {}
        self._fire_tokens: dict[tuple[str, str], int] = {}
        self.deleted_for_task: list[str] = []

    async def upsert(self, trigger: TaskTrigger) -> TaskTrigger:
        self._triggers[trigger.trigger_id] = trigger
        return trigger

    async def get(self, trigger_id: str) -> TaskTrigger | None:
        return self._triggers.get(trigger_id)

    async def list_for_task(self, task_id: str) -> list[TaskTrigger]:
        return [t for t in self._triggers.values() if t.task_id == task_id]

    async def list_for_tasks(self, task_ids) -> dict[str, list[TaskTrigger]]:  # noqa: ANN001
        wanted = set(task_ids)
        found: dict[str, list[TaskTrigger]] = {}
        for trigger in self._triggers.values():
            if trigger.task_id in wanted:
                found.setdefault(trigger.task_id, []).append(trigger)
        return found

    async def delete_for_task(self, task_id: str) -> int:
        self.deleted_for_task.append(task_id)
        to_delete = [tid for tid, t in self._triggers.items() if t.task_id == task_id]
        for tid in to_delete:
            del self._triggers[tid]
        return len(to_delete)

    async def delete(self, trigger_id: str) -> bool:
        return self._triggers.pop(trigger_id, None) is not None

    async def get_by_webhook_id(self, webhook_id: str) -> TaskTrigger | None:
        for trig in self._triggers.values():
            if trig.webhook_id == webhook_id:
                return trig
        return None

    async def list_by_event_type(self, org_id: str, event_type: str) -> list[TaskTrigger]:
        return [
            t for t in self._triggers.values()
            if t.org_id == org_id and t.enabled
            and t.event_filter is not None and t.event_filter.get("event_type") == event_type
        ]

    async def claim_fire(
        self, trigger_id: str, *, fire_at: str, dedupe_token: str,
    ) -> int | None:
        trigger = self._triggers.get(trigger_id)
        if trigger is None:
            return None
        prior = self._fire_tokens.get((trigger_id, dedupe_token))
        if prior is not None:
            return prior
        if trigger.is_exhausted():
            return None
        updated = trigger.model_copy(update={
            "run_count": trigger.run_count + 1, "last_fire_at": fire_at,
        })
        self._triggers[trigger_id] = updated
        self._fire_tokens[(trigger_id, dedupe_token)] = updated.run_count
        return updated.run_count


class FakeWebhookSecretStore:
    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}
        self.stored: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    async def store(self, webhook_id: str, secret: str) -> None:
        self.stored.append((webhook_id, secret))
        self._secrets[webhook_id] = secret

    async def get(self, webhook_id: str) -> str | None:
        return self._secrets.get(webhook_id)

    async def delete(self, webhook_id: str) -> None:
        self.deleted.append(webhook_id)
        self._secrets.pop(webhook_id, None)


class FakeRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, TaskRun] = {}
        self._by_idempotency: dict[str, str] = {}

    async def create_if_absent(self, run: TaskRun) -> TaskRun | None:
        if run.idempotency_key in self._by_idempotency:
            return None
        self._runs[run.run_id] = run
        self._by_idempotency[run.idempotency_key] = run.run_id
        return run

    async def get(self, run_id: str) -> TaskRun | None:
        return self._runs.get(run_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> TaskRun | None:
        run_id = self._by_idempotency.get(idempotency_key)
        return self._runs.get(run_id) if run_id else None

    async def update(self, run: TaskRun) -> TaskRun:
        self._runs[run.run_id] = run
        return run

    async def resume_with_answer(self, run_id: str, *, answer: str) -> TaskRun | None:
        run = self._runs.get(run_id)
        if run is None or run.status != RunStatus.AWAITING_INPUT:
            return None
        updated = run.model_copy(update={"status": RunStatus.PENDING, "pending_answer": answer})
        self._runs[run_id] = updated
        return updated

    async def list_for_task(self, task_id: str, *, limit: int = 50, offset: int = 0) -> object:
        from app.services.tasks.domain.models import Page

        items = [r for r in self._runs.values() if r.task_id == task_id]
        return Page(items=items[offset:offset + limit], total=len(items), limit=limit, offset=offset)

    async def list_awaiting_event(self, org_id: str, event_type: str) -> list[TaskRun]:
        return [
            run for run in self._runs.values()
            if run.org_id == org_id
            and run.status == RunStatus.AWAITING_INPUT
            and run.awaiting_event_type == event_type
        ]

    async def list_expired_suspensions(self, *, now: object, limit: int = 100) -> list[TaskRun]:
        return []


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_event(self, *, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        self.sent.append({"topic": topic, "event_type": event_type, "payload": payload, "key": key})
        return True


class FakeGraphProviderForPrereqs:
    def __init__(self, *, connector_ok: bool = True) -> None:
        self._connector_ok = connector_ok

    async def get_user_connector_instances(self, **kwargs: object) -> list[dict]:
        if self._connector_ok:
            return [{"_key": "conn-1", "isConfigured": True, "isAuthenticated": True}]
        return []

    async def get_user_kb_permission(self, kb_id: str, user_id: str) -> str | None:
        return "reader"


_FIXED_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _make_engine(
    *,
    prerequisite_validator: PrerequisiteValidator | None = None,
    clock: FixedClock | None = None,
    webhook_secret_store: FakeWebhookSecretStore | None = None,
) -> tuple[TaskEngine, FakeTaskStore, FakeTriggerStore, FakeRunStore, FakeProducer]:
    task_store = FakeTaskStore()
    trigger_store = FakeTriggerStore()
    run_store = FakeRunStore()
    producer = FakeProducer()
    engine = TaskEngine(
        task_store=task_store, trigger_store=trigger_store, run_store=run_store,
        producer=producer, prerequisite_validator=prerequisite_validator,
        webhook_secret_store=webhook_secret_store,
        clock=clock or FixedClock(_FIXED_NOW),
    )
    return engine, task_store, trigger_store, run_store, producer


class TestCreate:
    async def test_create_without_prerequisites_or_triggers(self) -> None:
        engine, task_store, _trigger_store, _run_store, _producer = _make_engine()
        task, triggers, check_result, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="Daily digest", description="summarize", instructions="Summarize yesterday's tickets",
        )
        assert task.status == TaskStatus.ACTIVE
        assert task.task_id in task_store._tasks
        assert triggers == []
        assert check_result is None

    async def test_create_with_cron_trigger_computes_next_run_at(self) -> None:
        engine, _task_store, trigger_store, _run_store, _producer = _make_engine()
        _task, triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "cron", "cron_expression": "0 9 * * *"}],
        )
        assert len(triggers) == 1
        assert triggers[0].next_run_at is not None
        assert await trigger_store.get(triggers[0].trigger_id) == triggers[0]

    async def test_create_rejects_invalid_step_dag(self) -> None:
        engine, _task_store, _trigger_store, _run_store, _producer = _make_engine()
        with pytest.raises(TaskDAGError):
            await engine.create(
                org_id="org-1", user_id="user-1", user_email="a@b.com",
                title="t", description="d", instructions="i",
                steps=[TaskStep(id="a", description="x", depends_on=["a"])],
            )

    async def test_create_raises_prerequisite_error_and_does_not_persist(self) -> None:
        engine, task_store, _trigger_store, _run_store, _producer = _make_engine(
            prerequisite_validator=PrerequisiteValidator(),
        )
        graph_provider = FakeGraphProviderForPrereqs(connector_ok=False)
        with pytest.raises(PrerequisiteError):
            await engine.create(
                org_id="org-1", user_id="user-1", user_email="a@b.com",
                title="t", description="d", instructions="i",
                connector_ids=["conn-missing"], graph_provider=graph_provider,
            )
        assert task_store._tasks == {}

    async def test_create_succeeds_when_prerequisites_pass(self) -> None:
        engine, task_store, _trigger_store, _run_store, _producer = _make_engine(
            prerequisite_validator=PrerequisiteValidator(),
        )
        graph_provider = FakeGraphProviderForPrereqs(connector_ok=True)
        task, _triggers, check_result, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            connector_ids=["conn-1"], graph_provider=graph_provider,
        )
        assert task.task_id in task_store._tasks
        assert check_result is not None
        assert check_result.ok is True

    async def test_skip_prerequisite_check_bypasses_validator(self) -> None:
        engine, task_store, _trigger_store, _run_store, _producer = _make_engine(
            prerequisite_validator=PrerequisiteValidator(),
        )
        graph_provider = FakeGraphProviderForPrereqs(connector_ok=False)
        task, _triggers, check_result, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            connector_ids=["conn-missing"], graph_provider=graph_provider,
            skip_prerequisite_check=True,
        )
        assert task.task_id in task_store._tasks
        assert check_result is None

    async def test_non_blocking_mcp_issue_does_not_raise(self) -> None:
        engine, task_store, _trigger_store, _run_store, _producer = _make_engine(
            prerequisite_validator=PrerequisiteValidator(),
        )
        graph_provider = FakeGraphProviderForPrereqs()
        task, _triggers, check_result, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            mcp_server_ids=["mcp-1"], graph_provider=graph_provider,
        )
        assert task.task_id in task_store._tasks
        assert check_result is not None
        assert check_result.ok is True
        assert len(check_result.issues) == 1


class TestLifecycle:
    async def _create_task(self, engine: TaskEngine) -> TaskDefinition:
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        return task

    async def test_pause_sets_paused_and_disabled(self) -> None:
        engine, *_ = _make_engine()
        task = await self._create_task(engine)
        paused = await engine.pause(task.task_id, "org-1")
        assert paused.status == TaskStatus.PAUSED
        assert paused.enabled is False

    async def test_unpause_reactivates(self) -> None:
        engine, *_ = _make_engine()
        task = await self._create_task(engine)
        await engine.pause(task.task_id, "org-1")
        resumed = await engine.unpause(task.task_id, "org-1")
        assert resumed.status == TaskStatus.ACTIVE
        assert resumed.enabled is True

    async def test_cancel_marks_cancelled_and_removes_triggers(self) -> None:
        engine, _task_store, trigger_store, _run_store, _producer = _make_engine()
        task, triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "cron", "cron_expression": "0 9 * * *"}],
        )
        assert len(triggers) == 1
        cancelled = await engine.cancel(task.task_id, "org-1")
        assert cancelled.status == TaskStatus.CANCELLED
        assert cancelled.enabled is False
        assert await trigger_store.list_for_task(task.task_id) == []

    async def test_get_missing_task_raises(self) -> None:
        engine, *_ = _make_engine()
        with pytest.raises(TaskNotFoundError):
            await engine.get("nonexistent", "org-1")

    async def test_update_fields_partial_update(self) -> None:
        engine, *_ = _make_engine()
        task = await self._create_task(engine)
        updated = await engine.update_fields(task.task_id, "org-1", title="New title")
        assert updated.title == "New title"
        assert updated.description == task.description

    async def test_delete_removes_task_and_triggers(self) -> None:
        engine, task_store, trigger_store, _run_store, _producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "cron", "cron_expression": "0 9 * * *"}],
        )
        deleted = await engine.delete(task.task_id, "org-1")
        assert deleted is True
        assert task.task_id not in task_store._tasks
        assert trigger_store.deleted_for_task == [task.task_id]


class TestRunNow:
    async def test_run_now_creates_run_and_publishes_dispatch_event(self) -> None:
        engine, _task_store, _trigger_store, run_store, producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        run = await engine.run_now(task.task_id, "org-1")
        assert run.status == RunStatus.PENDING
        assert await run_store.get(run.run_id) == run
        assert len(producer.sent) == 1
        assert producer.sent[0]["payload"]["task_id"] == task.task_id
        assert producer.sent[0]["payload"]["run_id"] == run.run_id

    async def test_run_now_is_idempotent_within_the_same_instant(self) -> None:
        """Two rapid `run_now` calls at the same fixed clock instant must
        collapse to the same run rather than dispatching twice."""
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        engine, _task_store, _trigger_store, _run_store, producer = _make_engine(clock=clock)
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        first = await engine.run_now(task.task_id, "org-1")
        second = await engine.run_now(task.task_id, "org-1")
        assert first.run_id == second.run_id
        assert len(producer.sent) == 1


class TestGetRun:
    async def test_returns_the_run_for_its_own_task_and_org(self) -> None:
        engine, _task_store, _trigger_store, _run_store, _producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        run = await engine.run_now(task.task_id, "org-1")

        fetched = await engine.get_run(run.run_id, task.task_id, "org-1")

        assert fetched.run_id == run.run_id

    async def test_raises_for_a_run_from_a_different_org(self) -> None:
        """A caller must not be able to read another org's run by guessing/
        supplying its `run_id` -- `ITaskRunStore.get` alone is keyed by
        `run_id` only, so this ownership check belongs in `TaskEngine`."""
        engine, _task_store, _trigger_store, _run_store, _producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        run = await engine.run_now(task.task_id, "org-1")

        with pytest.raises(RunNotFoundError):
            await engine.get_run(run.run_id, task.task_id, "org-2")

    async def test_raises_for_a_run_from_a_different_task(self) -> None:
        engine, _task_store, _trigger_store, _run_store, _producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        run = await engine.run_now(task.task_id, "org-1")

        with pytest.raises(RunNotFoundError):
            await engine.get_run(run.run_id, "some-other-task-id", "org-1")

    async def test_raises_for_an_unknown_run_id(self) -> None:
        engine, _task_store, _trigger_store, _run_store, _producer = _make_engine()

        with pytest.raises(RunNotFoundError):
            await engine.get_run("no-such-run", "no-such-task", "org-1")


class TestAnswerRun:
    async def test_answers_an_awaiting_input_run_and_republishes_dispatch(self) -> None:
        engine, _task_store, _trigger_store, run_store, producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        run = await engine.run_now(task.task_id, "org-1")
        await run_store.update(run.model_copy(update={
            "status": RunStatus.AWAITING_INPUT, "hil_question_id": "q-1", "checkpoint_id": "cp-1",
        }))
        producer.sent.clear()

        answered = await engine.answer_run(run.run_id, task.task_id, "org-1", "Sprint 42")

        assert answered.status == RunStatus.PENDING
        assert answered.pending_answer == "Sprint 42"
        assert len(producer.sent) == 1
        assert producer.sent[0]["payload"]["run_id"] == run.run_id

    async def test_raises_run_not_found_for_wrong_org(self) -> None:
        engine, _task_store, _trigger_store, run_store, _producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        run = await engine.run_now(task.task_id, "org-1")
        await run_store.update(run.model_copy(update={"status": RunStatus.AWAITING_INPUT}))

        with pytest.raises(RunNotFoundError):
            await engine.answer_run(run.run_id, task.task_id, "org-2", "Sprint 42")

    async def test_raises_stale_answer_error_when_run_is_not_awaiting_input(self) -> None:
        """A run that already completed, or was already answered, must
        reject a (re-)submitted answer rather than silently corrupting a
        different execution attempt."""
        engine, _task_store, _trigger_store, _run_store, _producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        run = await engine.run_now(task.task_id, "org-1")  # still PENDING, never paused

        with pytest.raises(StaleAnswerError):
            await engine.answer_run(run.run_id, task.task_id, "org-1", "Sprint 42")

    async def test_refuses_an_answer_past_the_replay_deadline(self) -> None:
        """The journal is gone by now, so resuming would re-run every step the
        first attempt already completed rather than continue from where it
        stopped. The run is failed instead, and stays failed."""
        engine, _task_store, _trigger_store, run_store, producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        run = await engine.run_now(task.task_id, "org-1")
        expired = _FIXED_NOW - timedelta(days=1)
        await run_store.update(run.model_copy(update={
            "status": RunStatus.AWAITING_INPUT,
            "suspension_kind": "approval",
            "resume_deadline_at": expired.isoformat(),
        }))
        producer.sent.clear()

        with pytest.raises(ExpiredSuspensionError):
            await engine.answer_run(run.run_id, task.task_id, "org-1", "yes")

        stored = await run_store.get(run.run_id)
        assert stored.status == RunStatus.FAILED
        assert producer.sent == []

    async def test_allows_an_answer_before_the_replay_deadline(self) -> None:
        engine, _task_store, _trigger_store, run_store, _producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        run = await engine.run_now(task.task_id, "org-1")
        await run_store.update(run.model_copy(update={
            "status": RunStatus.AWAITING_INPUT,
            "resume_deadline_at": (_FIXED_NOW + timedelta(days=29)).isoformat(),
        }))

        answered = await engine.answer_run(run.run_id, task.task_id, "org-1", "yes")
        assert answered.status == RunStatus.PENDING

    async def test_second_answer_to_the_same_question_is_rejected_as_stale(self) -> None:
        engine, _task_store, _trigger_store, run_store, _producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )
        run = await engine.run_now(task.task_id, "org-1")
        await run_store.update(run.model_copy(update={"status": RunStatus.AWAITING_INPUT}))

        first = await engine.answer_run(run.run_id, task.task_id, "org-1", "first answer")
        assert first.status == RunStatus.PENDING

        with pytest.raises(StaleAnswerError):
            await engine.answer_run(run.run_id, task.task_id, "org-1", "second, late answer")


class TestPromoteToAgent:
    async def test_promote_delegates_and_persists_promoted_agent_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine, task_store, _trigger_store, _run_store, _producer = _make_engine()
        task, _triggers, _check, _webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
        )

        async def fake_create_agent_from_task(task_arg: TaskDefinition, *, graph_provider: object, config_service: object) -> str:
            assert task_arg.task_id == task.task_id
            return "agent-123"

        monkeypatch.setattr(
            "app.services.tasks.application.promote_to_agent.create_agent_from_task",
            fake_create_agent_from_task,
        )
        agent_id = await engine.promote_to_agent(
            task.task_id, "org-1", graph_provider=object(), config_service=object(),
        )
        assert agent_id == "agent-123"
        assert task_store._tasks[task.task_id].promoted_agent_id == "agent-123"


class TestCreateWebhookTrigger:
    async def test_generates_webhook_id_and_secret(self) -> None:
        secret_store = FakeWebhookSecretStore()
        engine, _task_store, trigger_store, _run_store, _producer = _make_engine(webhook_secret_store=secret_store)

        task, triggers, _check, webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "webhook"}],
        )

        assert len(triggers) == 1
        trigger = triggers[0]
        assert trigger.webhook_id is not None
        assert trigger.trigger_id in webhook_secrets
        assert secret_store.stored == [(trigger.webhook_id, webhook_secrets[trigger.trigger_id])]
        assert await trigger_store.get_by_webhook_id(trigger.webhook_id) is not None
        assert task.task_id == trigger.task_id

    async def test_ignores_caller_supplied_webhook_id(self) -> None:
        """`webhook_id` must always be server-generated -- a caller-supplied
        value (e.g. a chat user guessing at the field) must never be
        trusted, since it would let one org's trigger point at a webhook_id
        another org's secret was minted for."""
        secret_store = FakeWebhookSecretStore()
        engine, *_ = _make_engine(webhook_secret_store=secret_store)

        _task, triggers, _check, _secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "webhook", "webhook_id": "attacker-supplied-id"}],
        )

        assert triggers[0].webhook_id != "attacker-supplied-id"

    async def test_raises_without_a_configured_secret_store(self) -> None:
        engine, *_ = _make_engine(webhook_secret_store=None)

        with pytest.raises(InvalidTriggerError):
            await engine.create(
                org_id="org-1", user_id="user-1", user_email="a@b.com",
                title="t", description="d", instructions="i",
                triggers=[{"kind": "webhook"}],
            )

    async def test_invalid_event_trigger_spec_raises_invalid_trigger_error(self) -> None:
        engine, *_ = _make_engine()

        with pytest.raises(InvalidTriggerError):
            await engine.create(
                org_id="org-1", user_id="user-1", user_email="a@b.com",
                title="t", description="d", instructions="i",
                triggers=[{"kind": "event", "event_filter": {"connectorId": "conn-1"}}],
            )

    async def test_trigger_spec_missing_kind_raises_invalid_trigger_error_not_key_error(self) -> None:
        """Regression: a chat-agent tool call that omits/misnames `kind`
        (e.g. `{"type": "one_time", ...}`) must surface an actionable
        `InvalidTriggerError` the agent can read and self-correct from --
        not a bare `KeyError` that reaches the caller as an opaque
        "Failed: 'kind'" with no indication of what to fix."""
        engine, *_ = _make_engine()

        with pytest.raises(InvalidTriggerError, match="kind"):
            await engine.create(
                org_id="org-1", user_id="user-1", user_email="a@b.com",
                title="t", description="d", instructions="i",
                triggers=[{"type": "one_time", "fire_at": "2024-01-01T09:00:00+00:00"}],
            )

    async def test_non_dict_trigger_spec_raises_invalid_trigger_error(self) -> None:
        engine, *_ = _make_engine()

        with pytest.raises(InvalidTriggerError):
            await engine.create(
                org_id="org-1", user_id="user-1", user_email="a@b.com",
                title="t", description="d", instructions="i",
                triggers=["one_time"],
            )

    async def test_valid_event_trigger_is_created(self) -> None:
        engine, *_ = _make_engine()

        _task, triggers, _check, webhook_secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "event", "event_filter": {"event_type": "record.created"}}],
        )

        assert len(triggers) == 1
        assert triggers[0].kind == TriggerKind.EVENT
        assert webhook_secrets == {}


class TestSetTriggerEnabled:
    async def _create_cron_trigger(self, engine: TaskEngine) -> TaskTrigger:
        _task, triggers, _check, _secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "cron", "cron_expression": "0 9 * * *"}],
        )
        return triggers[0]

    async def test_disabling_clears_the_trigger_from_firing(self) -> None:
        engine, *_ = _make_engine()
        trigger = await self._create_cron_trigger(engine)

        updated = await engine.set_trigger_enabled(
            trigger.trigger_id, "org-1", enabled=False,
        )

        assert updated.enabled is False

    async def test_re_enabling_recomputes_next_run_at(self) -> None:
        """A trigger disabled for long enough that its stored `next_run_at`
        is in the past must not come back due -- it should resume on its
        normal schedule."""
        engine, *_ = _make_engine()
        trigger = await self._create_cron_trigger(engine)
        await engine.set_trigger_enabled(trigger.trigger_id, "org-1", enabled=False)

        updated = await engine.set_trigger_enabled(
            trigger.trigger_id, "org-1", enabled=True,
        )

        assert updated.enabled is True
        assert updated.next_run_at is not None
        assert datetime.fromisoformat(updated.next_run_at) > _FIXED_NOW

    async def test_another_orgs_trigger_is_not_found(self) -> None:
        engine, *_ = _make_engine()
        trigger = await self._create_cron_trigger(engine)

        with pytest.raises(TriggerNotFoundError):
            await engine.set_trigger_enabled(
                trigger.trigger_id, "org-2", enabled=False,
            )

    async def test_a_trigger_belonging_to_another_task_is_not_found(self) -> None:
        """The workflow-scoped REST route passes the workflow it was called
        on; without this check a caller could toggle any trigger in their org
        through a workflow they happen to own."""
        engine, *_ = _make_engine()
        trigger = await self._create_cron_trigger(engine)

        with pytest.raises(TriggerNotFoundError):
            await engine.set_trigger_enabled(
                trigger.trigger_id, "org-1", enabled=False, task_id="some-other-task",
            )


class TestFireTrigger:
    async def _create_task_with_webhook_trigger(
        self, engine: TaskEngine,
    ) -> tuple[TaskDefinition, TaskTrigger]:
        task, triggers, _check, _secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "webhook"}],
        )
        return task, triggers[0]

    async def test_fires_and_dispatches_a_run(self) -> None:
        engine, _task_store, trigger_store, run_store, producer = _make_engine(
            webhook_secret_store=FakeWebhookSecretStore(),
        )
        _task, trigger = await self._create_task_with_webhook_trigger(engine)

        run = await engine.fire_trigger(trigger.trigger_id)

        assert run.status == RunStatus.PENDING
        assert run.trigger_id == trigger.trigger_id
        assert await run_store.get(run.run_id) == run
        assert len(producer.sent) == 1

        updated_trigger = await trigger_store.get(trigger.trigger_id)
        assert updated_trigger.run_count == 1
        assert updated_trigger.last_fire_at is not None

    async def test_unknown_trigger_raises(self) -> None:
        engine, *_ = _make_engine()
        with pytest.raises(TriggerNotFoundError):
            await engine.fire_trigger("no-such-trigger")

    async def test_disabled_trigger_raises(self) -> None:
        engine, _task_store, trigger_store, _run_store, _producer = _make_engine(
            webhook_secret_store=FakeWebhookSecretStore(),
        )
        _task, trigger = await self._create_task_with_webhook_trigger(engine)
        await trigger_store.upsert(trigger.model_copy(update={"enabled": False}))

        with pytest.raises(InvalidTriggerError):
            await engine.fire_trigger(trigger.trigger_id)

    async def test_paused_task_raises(self) -> None:
        engine, _task_store, _trigger_store, _run_store, _producer = _make_engine(
            webhook_secret_store=FakeWebhookSecretStore(),
        )
        task, trigger = await self._create_task_with_webhook_trigger(engine)
        await engine.pause(task.task_id, "org-1")

        with pytest.raises(InvalidTriggerError):
            await engine.fire_trigger(trigger.trigger_id)

    async def test_repeated_fires_are_idempotent_within_the_same_instant(self) -> None:
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        engine, _task_store, _trigger_store, _run_store, producer = _make_engine(
            webhook_secret_store=FakeWebhookSecretStore(), clock=clock,
        )
        _task, trigger = await self._create_task_with_webhook_trigger(engine)

        first = await engine.fire_trigger(trigger.trigger_id)
        second = await engine.fire_trigger(trigger.trigger_id)

        assert first.run_id == second.run_id
        assert len(producer.sent) == 1


class TestFireEvent:
    async def test_dispatches_matching_trigger(self) -> None:
        engine, *_ = _make_engine()
        _task, triggers, _check, _secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "event", "event_filter": {"event_type": "record.created"}}],
        )

        runs = await engine.fire_event("org-1", "record.created", {"event_type": "record.created"})

        assert len(runs) == 1
        assert runs[0].trigger_id == triggers[0].trigger_id

    async def test_extra_filter_keys_must_match_payload(self) -> None:
        engine, *_ = _make_engine()
        await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "event", "event_filter": {"event_type": "record.created", "connectorId": "conn-1"}}],
        )

        matching = await engine.fire_event("org-1", "record.created", {"event_type": "record.created", "connectorId": "conn-1"})
        non_matching = await engine.fire_event("org-1", "record.created", {"event_type": "record.created", "connectorId": "conn-2"})

        assert len(matching) == 1
        assert non_matching == []

    async def test_no_matching_triggers_returns_empty(self) -> None:
        engine, *_ = _make_engine()
        runs = await engine.fire_event("org-1", "record.deleted", {"event_type": "record.deleted"})
        assert runs == []

    async def test_one_failing_trigger_does_not_block_the_others(self) -> None:
        """Best-effort dispatch -- e.g. one trigger's parent task was
        deleted after the trigger's own index entry was created (a stale
        index is possible if a future migration only partially cleans up)
        must not prevent OTHER matching triggers in the same batch from
        firing."""
        engine, task_store, trigger_store, _run_store, _producer = _make_engine()
        _task, triggers, _check, _secrets = await engine.create(
            org_id="org-1", user_id="user-1", user_email="a@b.com",
            title="t", description="d", instructions="i",
            triggers=[{"kind": "event", "event_filter": {"event_type": "record.created"}}],
        )
        # Simulate a stale index entry: the parent task ("ghost-task") was
        # never created in `task_store`, but the trigger (and its
        # event-type index membership) still exists.
        broken_trigger = TaskTrigger(
            task_id="ghost-task", org_id="org-1", kind=TriggerKind.EVENT,
            event_filter={"event_type": "record.created"},
        )
        await trigger_store.upsert(broken_trigger)
        assert "ghost-task" not in task_store._tasks

        runs = await engine.fire_event("org-1", "record.created", {"event_type": "record.created"})

        assert len(runs) == 1
        assert runs[0].trigger_id == triggers[0].trigger_id
