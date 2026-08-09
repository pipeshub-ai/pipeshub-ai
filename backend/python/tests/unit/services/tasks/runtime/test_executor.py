"""Unit tests for `TaskExecutor` -- claim/execute/finalize state machine,
crash recovery (retry vs DLQ via the side-effect gate), auto-disable, and
the abandoned-run reaper.

Uses the real `RedisRunStore` over `fakeredis` (same as
`test_scheduler_loop.py`) so lease/claim semantics are exercised for real,
plus a plain in-memory `FakeTaskStore` (the graph contract is already
proven by `test_task_store_contract.py`) and a `FakeSpecAssembler` that
hands back a scripted `FakeAgent` instead of building a real one --
`TaskSpecAssembler`'s own `TaskDefinition` -> `Agent` wiring is proven
separately by `test_spec_assembler.py` / `test_headless_execution.py`.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fakeredis import aioredis as fake_aioredis

from app.agent_loop_lib.core.types import AgentResult, Goal, RunUsage
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.modules.providers.budget.base import BudgetSnapshot
from app.agent_loop_lib.modules.stores.checkpoint.base import AgentCheckpoint
from app.agent_loop_lib.modules.stores.checkpoint.graph_store import (
    GraphCheckpointStore,
)
from app.agent_loop_lib.modules.stores.state.base import AgentStatus
from app.agent_loop_lib.modules.stores.timeline.base import TimelineEntry
from app.agent_loop_lib.modules.stores.timeline.graph_store import GraphTimelineStore
from app.services.messaging.config import StreamMessage
from app.services.tasks.adapters.redis.run_store import RedisRunStore
from app.services.tasks.domain.errors import (
    OptimisticConcurrencyError,
    ToolResolutionError,
)
from app.services.tasks.domain.models import (
    RunStatus,
    TaskDefinition,
    TaskPrincipal,
    TaskRun,
    TaskStatus,
    compute_idempotency_key,
)
from app.services.tasks.domain.policies import BudgetPolicy, RetryPolicy
from app.services.tasks.interface.clock import FixedClock
from app.services.tasks.interface.notifier import TaskNotificationKind
from app.services.tasks.runtime.executor import TaskExecutor, _suspension_summary
from tests.unit.agent_loop_lib.modules.stores.fakes import FakeGraphProvider


class FakeTaskStore:
    """Plain in-memory `ITaskStore` -- the graph adapter's own correctness
    is proven by `test_task_store_contract.py`; this fake just needs to
    honor optimistic concurrency so `TaskExecutor`'s update-retry loops
    are exercised faithfully."""

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

    async def list(self, query) -> None:  # pragma: no cover - unused by these tests
        raise NotImplementedError


class FakeMessagingProducer:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def initialize(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        return await self.send_event(topic, "message", message, key=key)

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        self.sent.append({"topic": topic, "event_type": event_type, "payload": payload, "key": key})
        return True


class FakeTaskNotifier:
    def __init__(self) -> None:
        self.notifications: list = []

    async def notify(self, notification) -> None:
        self.notifications.append(notification)


class FakeAgent:
    """Stands in for `agent_loop_lib.agent.Agent` -- `TaskExecutor` only
    ever touches `run_ctx.run_id`, `run(goal)`, `resume(checkpoint_id)`,
    and (Phase 7) `runtime.hooks` (to wire `track_side_effects`), so
    that's the entire surface this fake needs to provide. `runtime.hooks`
    is a real `HookRegistry`, not a stub, since `track_side_effects`
    calls `.on(event).use(...)` on it."""

    def __init__(self, run_id: str, *, result: AgentResult | None = None, raise_exc: Exception | None = None) -> None:
        self.run_ctx = SimpleNamespace(run_id=run_id)
        self.runtime = SimpleNamespace(hooks=HookRegistry())
        self._result = result
        self._raise_exc = raise_exc
        self.run_calls = 0
        self.resume_calls = 0
        self.resume_checkpoint_ids: list[str] = []
        self.resume_hil_responses: list[dict[str, str] | None] = []

    async def run(self, goal: Goal) -> AgentResult:
        self.run_calls += 1
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._result is not None
        return self._result

    async def resume(self, checkpoint_id: str, hil_responses: dict[str, str] | None = None) -> AgentResult:
        self.resume_calls += 1
        self.resume_checkpoint_ids.append(checkpoint_id)
        self.resume_hil_responses.append(hil_responses)
        if self._raise_exc is not None:
            raise self._raise_exc
        assert self._result is not None
        return self._result


class FakeSpecAssembler:
    def __init__(self) -> None:
        self.next_agent: FakeAgent | None = None
        self.assembled_tasks: list[TaskDefinition] = []
        self.raise_exc: Exception | None = None

    async def assemble(self, task: TaskDefinition, **_kwargs) -> tuple[FakeAgent, Goal]:
        self.assembled_tasks.append(task)
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.next_agent is not None, "test must set fake_assembler.next_agent first"
        return self.next_agent, Goal(description=task.instructions)


class _RecordingConversationWriter:
    def __init__(self) -> None:
        self.appended: list = []

    async def append_result(self, conversation_id: str, org_id: str, msg) -> None:  # noqa: ANN001
        self.appended.append(msg)

    async def aclose(self) -> None:
        pass


def _make_task(**overrides) -> TaskDefinition:
    defaults = {
        "org_id": "org-1",
        "created_by_user_id": "user-1",
        "principal": TaskPrincipal(org_id="org-1", user_id="user-1", user_email="a@b.com"),
        "title": "Daily digest",
        "description": "summarize tickets",
        "instructions": "Summarize yesterday's tickets",
        "status": TaskStatus.ACTIVE,
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return TaskDefinition(**defaults)


def _make_run(task: TaskDefinition, **overrides) -> TaskRun:
    fire_time = overrides.pop("fire_time", "2024-01-01T09:00:00+00:00")
    defaults = {
        "task_id": task.task_id,
        "org_id": task.org_id,
        "idempotency_key": compute_idempotency_key(task.task_id, fire_time),
        "scheduled_for": fire_time,
        "created_at": fire_time,
    }
    defaults.update(overrides)
    return TaskRun(**defaults)


async def _drain_background_tasks(executor: TaskExecutor) -> None:
    """Awaits every currently-tracked background task (retry-republish
    timers, the per-run heartbeat loop) to completion, ignoring
    cancellation -- the heartbeat loop is cancelled as part of normal
    execution teardown and its `CancelledError` is not the thing these
    tests care about."""
    for task_obj in list(executor._background_tasks):
        with contextlib.suppress(asyncio.CancelledError):
            await task_obj


def _dispatch_message(run: TaskRun) -> StreamMessage:
    return StreamMessage(eventType="task_run_dispatch", payload={
        "run_id": run.run_id, "task_id": run.task_id, "org_id": run.org_id,
    })


@pytest.fixture
async def redis_client() -> fake_aioredis.FakeRedis:
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def run_store(redis_client) -> RedisRunStore:
    return RedisRunStore(redis_client)


@pytest.fixture
def task_store() -> FakeTaskStore:
    return FakeTaskStore()


@pytest.fixture
def checkpoint_store() -> GraphCheckpointStore:
    return GraphCheckpointStore(FakeGraphProvider(), org_id="org-1")


@pytest.fixture
def timeline_store() -> GraphTimelineStore:
    return GraphTimelineStore(FakeGraphProvider(), org_id="org-1")


@pytest.fixture
def producer() -> FakeMessagingProducer:
    return FakeMessagingProducer()


@pytest.fixture
def notifier() -> FakeTaskNotifier:
    return FakeTaskNotifier()


@pytest.fixture
def spec_assembler() -> FakeSpecAssembler:
    return FakeSpecAssembler()


def _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock, **overrides) -> TaskExecutor:
    kwargs = {
        "task_store": task_store,
        "run_store": run_store,
        "checkpoint_store_factory": lambda _org_id: checkpoint_store,
        "spec_assembler": spec_assembler,
        "graph_provider": object(),
        "config_service": object(),
        "producer": producer,
        "notifier": notifier,
        "clock": clock,
        "owner": "executor-test",
        "heartbeat_interval_seconds": 999.0,
    }
    kwargs.update(overrides)
    return TaskExecutor(**kwargs)


class TestCheckpointStoreScoping:
    async def test_checkpoint_store_factory_is_called_with_the_run_s_own_org_id(
        self, task_store, run_store, spec_assembler, producer, notifier,
    ) -> None:
        """One `TaskExecutor` serves every org in the deployment;
        `GraphCheckpointStore` tags every write with (and `load` rejects a
        mismatched) a single `org_id` baked in at construction (see its own
        docstring). If the executor built one store at __init__ time and
        reused it for every run, every org except that one would either
        mislabel or lose its checkpoints. This asserts the fix: the store is
        built fresh, per run, from that run's own `task.principal.org_id`."""
        task = await task_store.create(_make_task(
            org_id="org-2",
            principal=TaskPrincipal(org_id="org-2", user_id="user-1", user_email="a@b.com"),
        ))
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), success=True,
        ))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        requested_org_ids: list[str] = []

        def factory(org_id: str) -> GraphCheckpointStore:
            requested_org_ids.append(org_id)
            return GraphCheckpointStore(FakeGraphProvider(), org_id=org_id)

        executor = _make_executor(
            task_store, run_store, None, spec_assembler, producer, notifier, clock,
            checkpoint_store_factory=factory,
        )

        result = await executor.handle_dispatch(_dispatch_message(run))

        assert result is True
        assert requested_org_ids == ["org-2"]
        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.SUCCEEDED


class TestSuccessfulExecution:
    async def test_dispatch_claims_executes_and_marks_succeeded(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), output="Posted digest", success=True, usage=RunUsage(requests=2),
        ))
        clock = FixedClock(datetime(2024, 1, 1, 9, 5, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        result = await executor.handle_dispatch(_dispatch_message(run))

        assert result is True
        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.SUCCEEDED
        assert fetched.agent_run_id == "agent-run-1"
        assert fetched.output_summary == "Posted digest"
        assert fetched.lease_owner is None
        assert fetched.usage == {"requests": 2, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}

        kinds = [n.kind for n in notifier.notifications]
        assert TaskNotificationKind.RUN_SUCCEEDED in kinds

    async def test_fresh_run_calls_run_not_resume(self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        agent = FakeAgent("agent-run-1", result=AgentResult(goal=Goal(description="x"), success=True))
        spec_assembler.next_agent = agent
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        assert agent.run_calls == 1
        assert agent.resume_calls == 0

    async def test_run_with_checkpoint_id_resumes_instead_of_restarting(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task, checkpoint_id="cp-123"))
        agent = FakeAgent("agent-run-1", result=AgentResult(goal=Goal(description="x"), success=True))
        spec_assembler.next_agent = agent
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        assert agent.resume_calls == 1
        assert agent.run_calls == 0

    async def test_checkpoint_id_persisted_after_run_from_real_store(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        agent = FakeAgent("agent-run-1", result=AgentResult(goal=Goal(description="x"), success=True))
        spec_assembler.next_agent = agent
        await checkpoint_store.save(AgentCheckpoint(
            run_id="agent-run-1", agent_id="a1", trace_id="t1", role_name="worker",
            model="m", goal=Goal(description="x"), messages=[], turn_index=0,
            budget_snapshot=BudgetSnapshot(),
        ))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.checkpoint_id is not None


class TestStepReportPersistence:
    """`TaskDagLoop` (spec_assembler.py) computes `completed_steps`/
    `failed_step_id`/`skipped_steps` and hands them to `agent.succeed`/
    `agent.fail` as `detail` -- the only place that survives is the
    timeline entry those calls append (`AgentResult` itself has no field
    for it). `_step_report_fields` is the bridge back onto `TaskRun`;
    these tests use a `FakeAgent` that never touches the timeline itself,
    so they pre-seed the entry a real DAG-driven `Agent.succeed`/`fail`
    would have appended, isolating the bridge logic from `TaskDagLoop`
    itself (proven separately by `test_task_dag_loop.py`)."""

    async def test_failed_and_skipped_steps_are_persisted_from_the_timeline_detail(
        self, task_store, run_store, checkpoint_store, timeline_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), success=False,
            error="1/2 step(s) completed. FAILED: fetch -- boom\nSkipped (a dependency failed): report",
        ))
        await timeline_store.append(TimelineEntry(
            sequence_id=1, trace_id="t1", run_id="agent-run-1", agent_id="agent-run-1",
            timestamp="2024-01-01T00:00:00+00:00", status=AgentStatus.FAILED, event_type="agent_failed",
            summary="failed", detail={"completed_steps": [], "failed_step_id": "fetch", "skipped_steps": ["report"]},
        ))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(
            task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock,
            timeline_store_factory=lambda _org_id: timeline_store,
        )

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.FAILED
        assert fetched.failed_step_id == "fetch"
        assert fetched.skipped_steps == ["report"]
        assert fetched.completed_steps == []

    async def test_no_timeline_store_leaves_step_fields_at_their_defaults(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        """No `timeline_store_factory` configured (today's default for a
        deployment that hasn't wired one) must not crash `_finalize_result`
        -- same graceful-degradation posture as every other optional
        dependency on this class."""
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), success=True,
        ))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.SUCCEEDED
        assert fetched.completed_steps == []
        assert fetched.failed_step_id is None
        assert fetched.skipped_steps == []


class TestNeedsInput:
    async def test_needs_input_marks_awaiting_input(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), success=True, needs_input="Which Slack channel?",
        ))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.AWAITING_INPUT
        assert fetched.output_summary == "Which Slack channel?"
        kinds = [n.kind for n in notifier.notifications]
        assert TaskNotificationKind.AWAITING_INPUT in kinds

    async def test_the_question_is_posted_to_the_originating_conversation(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        """A run that stops to ask something and says nothing in the chat it
        was started from looks hung: the only other trace is a notification."""
        task = await task_store.create(_make_task(created_from_conversation_id="conv-1"))
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), success=True, needs_input="Which Slack channel?",
        ))
        writer = _RecordingConversationWriter()
        executor = _make_executor(
            task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
            FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc)),
            conversation_writer=writer,
        )

        await executor.handle_dispatch(_dispatch_message(run))

        assert [m.status for m in writer.appended] == ["awaiting_input"]
        assert writer.appended[0].output_summary == "Which Slack channel?"


class TestSuspensionSummary:
    """What a suspended code workflow says it is waiting for.

    This one string is the notification, the chat card, and the run list
    entry, so `suspended:approval` left the person being asked with no idea
    what they were approving.
    """

    def test_an_approval_shows_the_question_that_was_asked(self) -> None:
        summary = _suspension_summary({
            "suspension_kind": "approval", "label": "Delete 42 stale Jira issues?",
        })
        assert summary == "Delete 42 stale Jira issues?"

    def test_an_approval_without_a_label_still_says_what_is_needed(self) -> None:
        summary = _suspension_summary({"suspension_kind": "approval", "label": "  "})
        assert "approval" in summary.lower()

    def test_an_event_wait_names_the_event(self) -> None:
        summary = _suspension_summary({
            "suspension_kind": "wait_for_event", "event_type": "github.pull_request.opened",
        })
        assert "github.pull_request.opened" in summary

    async def test_needs_input_records_the_checkpoint_s_hil_question_id(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        """`hil_question_id` is what a later `answer_run` call keys its
        `hil_responses` dict on (see `_run_agent`) -- it must come from the
        SAME checkpoint whose `checkpoint_id` was just persisted, not be
        left null/stale."""
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), success=True, needs_input="Which Slack channel?",
        ))
        spec_assembler.next_agent = agent
        await checkpoint_store.save(AgentCheckpoint(
            run_id="agent-run-1", agent_id="a1", trace_id="t1", role_name="worker",
            model="m", goal=Goal(description="x"), messages=[], turn_index=0,
            budget_snapshot=BudgetSnapshot(), hil_request_id="hil-req-1",
        ))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.AWAITING_INPUT
        assert fetched.hil_question_id == "hil-req-1"
        assert fetched.checkpoint_id is not None
        assert fetched.pending_answer is None


class TestAwaitingInputRoundTrip:
    """Task engine plan Phase 10: "Awaiting-input round trip -- run
    terminates, answer resumes from checkpoint" and "Stale answer
    rejection -- answering a superseded question is rejected". Drives the
    full `TaskEngine.answer_run` -> `ITaskRunStore.resume_with_answer` ->
    re-dispatch -> `TaskExecutor.handle_dispatch` -> `_run_agent` path,
    the same production wiring a real "user answers a task's question"
    request goes through -- not just `_run_agent`'s dispatch logic in
    isolation."""

    async def test_answering_resumes_with_the_answer_injected(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        pause_agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), success=True, needs_input="Which Slack channel?",
        ))
        spec_assembler.next_agent = pause_agent
        await checkpoint_store.save(AgentCheckpoint(
            run_id="agent-run-1", agent_id="a1", trace_id="t1", role_name="worker",
            model="m", goal=Goal(description="x"), messages=[], turn_index=0,
            budget_snapshot=BudgetSnapshot(), hil_request_id="hil-req-1",
        ))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))
        paused = await run_store.get(run.run_id)
        assert paused.status == RunStatus.AWAITING_INPUT

        from app.services.tasks.application.engine import TaskEngine

        engine = TaskEngine(task_store=task_store, trigger_store=None, run_store=run_store, producer=producer)
        answered = await engine.answer_run(run.run_id, task.task_id, task.org_id, "#support")
        assert answered.status == RunStatus.PENDING

        resume_agent = FakeAgent("agent-run-1", result=AgentResult(goal=Goal(description="x"), success=True))
        spec_assembler.next_agent = resume_agent
        assert len(producer.sent) == 1
        await executor.handle_dispatch(StreamMessage(eventType="task_run_dispatch", payload=producer.sent[0]["payload"]))

        assert resume_agent.resume_calls == 1
        assert resume_agent.resume_checkpoint_ids == [paused.checkpoint_id]
        assert resume_agent.resume_hil_responses == [{"hil-req-1": "#support"}]

        final = await run_store.get(run.run_id)
        assert final.status == RunStatus.SUCCEEDED
        assert final.pending_answer is None
        assert final.hil_question_id is None

    async def test_answering_a_run_that_already_completed_is_rejected_as_stale(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(goal=Goal(description="x"), success=True))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)
        await executor.handle_dispatch(_dispatch_message(run))
        assert (await run_store.get(run.run_id)).status == RunStatus.SUCCEEDED

        from app.services.tasks.application.engine import TaskEngine
        from app.services.tasks.domain.errors import StaleAnswerError

        engine = TaskEngine(task_store=task_store, trigger_store=None, run_store=run_store, producer=producer)
        with pytest.raises(StaleAnswerError):
            await engine.answer_run(run.run_id, task.task_id, task.org_id, "too late")

    async def test_second_answer_to_the_same_question_never_reaches_the_agent(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        """Two concurrent answers to the same paused question: only the
        first is accepted by `resume_with_answer`'s atomic AWAITING_INPUT
        -> PENDING transition; the second must be rejected before it can
        ever reach `_run_agent` and inject a second, conflicting
        `hil_responses` value."""
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), success=True, needs_input="Which Slack channel?",
        ))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)
        await executor.handle_dispatch(_dispatch_message(run))

        from app.services.tasks.application.engine import TaskEngine
        from app.services.tasks.domain.errors import StaleAnswerError

        engine = TaskEngine(task_store=task_store, trigger_store=None, run_store=run_store, producer=producer)
        await engine.answer_run(run.run_id, task.task_id, task.org_id, "#support")

        with pytest.raises(StaleAnswerError):
            await engine.answer_run(run.run_id, task.task_id, task.org_id, "#engineering")


class TestOrderlyFailure:
    async def test_orderly_failure_marks_failed_without_retry(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), success=False, error="hit max turns",
        ))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.FAILED
        assert fetched.error == "hit max turns"
        assert producer.sent == []  # no retry republish

        updated_task = await task_store.get(task.task_id, task.org_id)
        assert updated_task.consecutive_failure_count == 1


class TestUnresolvableTools:
    """`TaskSpecAssembler` raises `ToolResolutionError` when a task declares a
    tool the loaded registry does not have. The registry is a deterministic
    function of the org's toolsets, so retrying can only produce the same
    failure -- it has to land in the prerequisite path, not the crash path."""

    async def test_unresolvable_tool_fails_the_run_without_retrying(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task(
            tool_names=["web_search"], retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0),
        ))
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.raise_exc = ToolResolutionError(
            ["web_search"], {"web_search": ["dynamic__web_search"]},
        )
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))
        await _drain_background_tasks(executor)

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.FAILED
        assert fetched.attempt == 1
        assert producer.sent == []

    async def test_the_failure_names_the_tool_and_its_closest_match(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        """The run's `error` is what the user reads in chat, so a bare
        "prerequisites not met" would leave a one-word typo undiagnosable."""
        task = await task_store.create(_make_task(tool_names=["web_search"]))
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.raise_exc = ToolResolutionError(
            ["web_search"], {"web_search": ["dynamic__web_search"]},
        )
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert "web_search" in fetched.error
        assert "dynamic__web_search" in fetched.error
        kinds = [n.kind for n in notifier.notifications]
        assert TaskNotificationKind.PREREQUISITE_MISSING in kinds


class TestCrashRecovery:
    async def test_crash_with_attempts_remaining_schedules_retry(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task(retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0)))
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", raise_exc=RuntimeError("boom"))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))
        # Retry republish happens on a spawned background task; backoff=0
        # so it should complete almost immediately.
        await _drain_background_tasks(executor)

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.PENDING
        assert fetched.attempt == 2
        assert len(producer.sent) == 1
        assert producer.sent[0]["payload"]["run_id"] == run.run_id

    async def test_retries_exhausted_goes_to_dlq(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task(retry_policy=RetryPolicy(max_attempts=2)))
        run = await run_store.create_if_absent(_make_run(task, attempt=2))
        spec_assembler.next_agent = FakeAgent("agent-run-1", raise_exc=RuntimeError("boom"))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.DLQ
        assert "Retries exhausted" in fetched.error
        kinds = [n.kind for n in notifier.notifications]
        assert TaskNotificationKind.TASK_DLQ in kinds

    async def test_side_effect_with_no_checkpoint_goes_straight_to_dlq(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task(retry_policy=RetryPolicy(max_attempts=5)))
        run = await run_store.create_if_absent(_make_run(task, had_write_side_effect=True))
        spec_assembler.next_agent = FakeAgent("agent-run-1", raise_exc=RuntimeError("boom"))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.DLQ
        assert "Unsafe to retry" in fetched.error
        assert producer.sent == []  # never retried despite attempts remaining

    async def test_side_effect_with_checkpoint_still_retries(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task(retry_policy=RetryPolicy(max_attempts=5, backoff_seconds=0)))
        run = await run_store.create_if_absent(_make_run(
            task, had_write_side_effect=True, checkpoint_id="cp-existing",
        ))
        spec_assembler.next_agent = FakeAgent("agent-run-1", raise_exc=RuntimeError("boom"))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))
        await _drain_background_tasks(executor)

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.PENDING
        assert fetched.checkpoint_id == "cp-existing"  # preserved for the retry's resume


class TestAutoDisable:
    async def test_task_auto_disables_after_max_consecutive_failures(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task(budget=BudgetPolicy(max_consecutive_failures=2)))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        for i in range(2):
            run = await run_store.create_if_absent(_make_run(task, fire_time=f"2024-01-01T0{i}:00:00+00:00"))
            spec_assembler.next_agent = FakeAgent(f"agent-run-{i}", result=AgentResult(
                goal=Goal(description="x"), success=False, error="failed",
            ))
            await executor.handle_dispatch(_dispatch_message(run))

        updated_task = await task_store.get(task.task_id, task.org_id)
        assert updated_task.status == TaskStatus.DISABLED
        assert updated_task.enabled is False
        kinds = [n.kind for n in notifier.notifications]
        assert TaskNotificationKind.TASK_AUTO_DISABLED in kinds

    async def test_success_resets_consecutive_failure_count(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task(consecutive_failure_count=3))
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(goal=Goal(description="x"), success=True))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        updated_task = await task_store.get(task.task_id, task.org_id)
        assert updated_task.consecutive_failure_count == 0


class TestIdempotencyAndEdgeCases:
    async def test_duplicate_dispatch_only_executes_once(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(task))
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(goal=Goal(description="x"), success=True))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))
        await executor.handle_dispatch(_dispatch_message(run))

        assert len(spec_assembler.assembled_tasks) == 1

    async def test_missing_run_id_in_payload_is_a_noop(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)
        message = StreamMessage(eventType="task_run_dispatch", payload={})
        assert await executor.handle_dispatch(message) is True

    async def test_unknown_run_id_is_a_noop(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)
        message = StreamMessage(eventType="task_run_dispatch", payload={"run_id": "no-such-run"})
        assert await executor.handle_dispatch(message) is True

    async def test_missing_task_fails_the_run(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = _make_task()  # never persisted to task_store
        run = await run_store.create_if_absent(_make_run(task))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.FAILED
        assert "no longer exists" in fetched.error

    async def test_disabled_task_fails_the_run(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task(enabled=False, status=TaskStatus.DISABLED))
        run = await run_store.create_if_absent(_make_run(task))
        clock = FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.FAILED
        assert len(spec_assembler.assembled_tasks) == 0


class FakeCodeWorkflowRunner:
    """Stands in for `CodeWorkflowRunner`, whose own sandbox/bridge plumbing
    is covered by the workflow suites -- here only the bridge RESULT matters,
    because that dict is what the executor turns into a terminal run status."""

    def __init__(self, result: dict) -> None:
        self._result = result
        self.calls: list[dict] = []

    async def run(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self._result


def _code_task(**overrides) -> TaskDefinition:
    return _make_task(execution_kind="code", workflow_version_id="ver-1", **overrides)


def _code_executor(
    task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, runner, **overrides,
) -> TaskExecutor:
    executor = _make_executor(
        task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
        FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc)),
        code_workflow_runner=runner, **overrides,
    )
    # The per-run broker is built from the real registry in production; these
    # tests never reach a tool call, so a stub keeps them off that path.
    executor._build_run_broker = lambda _task: _noop_broker()  # type: ignore[method-assign]
    return executor


async def _noop_broker() -> object:
    return object()


class TestCodeWorkflowFinalization:
    """A bridge result of `{"status": "failed"}` -- a sandbox timeout, an
    unexpected child exit, or a harness error -- must never be recorded as a
    successful run. That misreport is worse than the failure itself: the
    schedule advances, the user is told it worked, and nothing retries."""

    async def test_failed_bridge_result_never_ends_as_succeeded(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_code_task(retry_policy=RetryPolicy(max_attempts=1)))
        run = await run_store.create_if_absent(_make_run(task))
        runner = FakeCodeWorkflowRunner({"status": "failed", "error": "Workflow timed out after 300s"})
        executor = _code_executor(
            task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, runner,
        )

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status is not RunStatus.SUCCEEDED
        assert fetched.status == RunStatus.DLQ
        assert "Workflow timed out after 300s" in fetched.error
        assert TaskNotificationKind.TASK_DLQ in [n.kind for n in notifier.notifications]

    async def test_failed_bridge_result_still_retries_when_attempts_remain(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(
            _code_task(retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0)),
        )
        run = await run_store.create_if_absent(_make_run(task))
        runner = FakeCodeWorkflowRunner({"status": "failed", "error": "boom"})
        executor = _code_executor(
            task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, runner,
        )

        await executor.handle_dispatch(_dispatch_message(run))
        await _drain_background_tasks(executor)

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.PENDING
        assert fetched.attempt == 2

    async def test_successful_bridge_result_marks_the_run_succeeded(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_code_task())
        run = await run_store.create_if_absent(_make_run(task))
        runner = FakeCodeWorkflowRunner({"status": "succeeded", "output": "posted 3 messages"})
        executor = _code_executor(
            task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, runner,
        )

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.SUCCEEDED
        assert fetched.output_summary == "posted 3 messages"

    async def test_suspension_records_the_step_key_needed_to_resume(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        """Without the parked step key the answer has nowhere to be journaled,
        so the resumed run replays into the same suspension forever."""
        task = await task_store.create(_code_task())
        run = await run_store.create_if_absent(_make_run(task))
        runner = FakeCodeWorkflowRunner({
            "status": "awaiting_input",
            "suspension_kind": "approval",
            "step_key": "ctx.request_approval:deploy#0",
        })
        executor = _code_executor(
            task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, runner,
        )

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.AWAITING_INPUT
        assert fetched.suspended_step_key == "ctx.request_approval:deploy#0"
        assert fetched.suspension_kind == "approval"

    async def test_a_code_workflow_never_silently_falls_back_to_the_agent_path(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        """Running the natural-language `instructions` through an LLM instead
        of the pinned code is a different workflow wearing the same name."""
        task = await task_store.create(_code_task())
        run = await run_store.create_if_absent(_make_run(task))
        executor = _make_executor(
            task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
            FixedClock(datetime(2024, 1, 1, tzinfo=timezone.utc)),
            code_workflow_runner=None,
        )

        await executor.handle_dispatch(_dispatch_message(run))

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.DLQ
        assert spec_assembler.assembled_tasks == []


class TestReaper:
    async def test_reaper_recovers_abandoned_run_with_attempts_remaining(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task(retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=0)))
        run = await run_store.create_if_absent(_make_run(
            task, status=RunStatus.RUNNING, lease_owner="dead-worker",
            lease_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        ))
        clock = FixedClock(datetime.now(timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        reaped_count = await executor.run_reaper_tick()
        await _drain_background_tasks(executor)

        assert reaped_count == 1
        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.PENDING
        assert fetched.attempt == 2
        assert len(producer.sent) == 1

    async def test_reaper_dlqs_abandoned_run_with_side_effect_and_no_checkpoint(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(
            task, status=RunStatus.RUNNING, lease_owner="dead-worker", had_write_side_effect=True,
            lease_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        ))
        clock = FixedClock(datetime.now(timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        await executor.run_reaper_tick()

        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.DLQ

    async def test_reaper_ignores_runs_with_active_lease(
        self, task_store, run_store, checkpoint_store, spec_assembler, producer, notifier,
    ) -> None:
        task = await task_store.create(_make_task())
        run = await run_store.create_if_absent(_make_run(
            task, status=RunStatus.RUNNING, lease_owner="alive-worker",
            lease_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        ))
        clock = FixedClock(datetime.now(timezone.utc))
        executor = _make_executor(task_store, run_store, checkpoint_store, spec_assembler, producer, notifier, clock)

        reaped_count = await executor.run_reaper_tick()

        assert reaped_count == 0
        fetched = await run_store.get(run.run_id)
        assert fetched.status == RunStatus.RUNNING
