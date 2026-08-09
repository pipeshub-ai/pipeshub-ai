"""End-to-end integration test for CODE workflows — the product's headline
path: describe a workflow in chat, review it as a dry run, let a trigger fire
it, and have the generated code call a real tool and report back.

Wires the real classes production uses — `TaskEngine`, `GraphTaskStore`,
`RedisTriggerStore`/`RedisRunStore` over fakeredis, `SchedulerLoop`,
`TaskExecutor`, `CodeWorkflowRunner`, and the real `PlatformBroker` — and
drives them from the chat tool (`workflow_manage`) exactly as the agent does.

Doubles are limited to the boundaries this test is not about: the LLM behind
`WorkflowBuilderAgent` (a fixed source string instead), the tool registry (so
"a real tool ran" is observable without a live connector), the graph/artifact
stores, and Node (the conversation writer). The sandbox subprocess is left
out too — `CodeWorkflowRunner` falls back to in-process exec — so this stays a
test of the engine's plumbing rather than of process isolation, which
`test_sandbox_confinement.py` covers on its own.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NamedTuple
from unittest.mock import AsyncMock

import pytest
from fakeredis import aioredis as fake_aioredis

from app.services.messaging.config import StreamMessage, Topic
from app.services.tasks.adapters.graph.task_store import GraphTaskStore
from app.services.tasks.adapters.redis.run_store import RedisRunStore
from app.services.tasks.adapters.redis.trigger_store import RedisTriggerStore
from app.services.tasks.application.engine import TaskEngine
from app.services.tasks.domain.models import RunStatus
from app.services.tasks.interface.clock import FixedClock
from app.services.tasks.runtime.executor import TaskExecutor
from app.services.tasks.runtime.scheduler_loop import SchedulerLoop
from app.services.workflows.domain.models import ArtifactRef, WorkflowVersion
from app.services.workflows.runtime.code_runner import CodeWorkflowRunner
from tests.unit.services.tasks.adapters.fakes import FakeGraphProvider

if TYPE_CHECKING:
    from app.agents.agent_loop.tools.tasks.workflow_manage import WorkflowManageTool

_ORG = "org-1"
_USER = "user-1"
_CONVERSATION = "conv-1"

# Uses the SDK through the injected `sdk` namespace: the in-process exec path
# bans `__import__`, and the generator emits import lines only for the sandbox.
_GENERATED_SOURCE = '''
@sdk.workflow(name="daily_digest")
async def daily_digest(ctx, trigger_payload):
    issues = await ctx.tool("jira__search_issues", jql="assignee = currentUser()")
    await ctx.tool("slack__post_message", channel="#general", text=str(issues))
    return f"posted {issues}"
'''


class InMemoryJournal:
    """In-memory `IExecutionJournal`."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], Any] = {}
        self._seq = 0

    async def append(self, entry: Any) -> None:
        key = (entry.run_id, entry.step_key)
        if key in self._entries:
            return
        self._seq += 1
        self._entries[key] = entry.model_copy(update={"seq": self._seq})

    async def lookup(self, run_id: str, step_key: str) -> Any | None:
        return self._entries.get((run_id, step_key))

    async def load(self, run_id: str) -> list[Any]:
        return sorted(
            (v for (r, _), v in self._entries.items() if r == run_id), key=lambda e: e.seq,
        )

    async def touch(self, run_id: str) -> str | None:
        return "2099-01-01T00:00:00+00:00"

    async def compact(self, run_id: str, upto_seq: int) -> None:
        self._entries = {
            k: v for k, v in self._entries.items()
            if not (k[0] == run_id and v.seq <= upto_seq)
        }


class _Tag(NamedTuple):
    key: str
    value: str


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    async def arun(self, arguments: dict[str, Any]) -> str:
        self.calls.append(arguments)
        return f"{self.name}:ok"


class FakeToolRegistry:
    """Stands in for the credential-bearing `ToolRegistry` the spec assembler
    builds; `category` tags are what the dry-run write skip keys off."""

    def __init__(self, tools: dict[str, list[str]]) -> None:
        self.tools = {name: FakeTool(name) for name in tools}
        self._tags = dict(tools)

    def get(self, name: str) -> FakeTool | None:
        return self.tools.get(name)

    def names(self) -> list[str]:
        return list(self.tools)

    def tags_for_name(self, name: str) -> tuple[_Tag, ...]:
        return tuple(_Tag("category", c) for c in self._tags.get(name, []))


class FakeCodeStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def put(self, workflow_id: str, org_id: str, source: bytes, *, content_type: str = "text/x-python") -> ArtifactRef:
        artifact_id = str(uuid.uuid4())
        self.blobs[artifact_id] = source
        return ArtifactRef(artifact_id=artifact_id)

    async def get(self, ref: ArtifactRef) -> bytes:
        return self.blobs[ref.artifact_id]

    async def delete(self, ref: ArtifactRef) -> bool:
        return self.blobs.pop(ref.artifact_id, None) is not None


class FakeVersionStore:
    def __init__(self) -> None:
        self.versions: dict[str, WorkflowVersion] = {}

    async def save(self, version: WorkflowVersion) -> WorkflowVersion:
        siblings = [v for v in self.versions.values() if v.workflow_id == version.workflow_id]
        stored = version.model_copy(update={"version_number": len(siblings) + 1})
        self.versions[stored.version_id] = stored
        return stored

    async def get(self, version_id: str, org_id: str) -> WorkflowVersion | None:
        version = self.versions.get(version_id)
        return version if version and version.org_id == org_id else None

    async def list_for_workflow(self, workflow_id: str, org_id: str, *, limit: int = 20, offset: int = 0) -> list[WorkflowVersion]:
        rows = sorted(
            (v for v in self.versions.values() if v.workflow_id == workflow_id and v.org_id == org_id),
            key=lambda v: v.version_number, reverse=True,
        )
        return rows[offset:offset + limit]

    async def get_latest(self, workflow_id: str, org_id: str) -> WorkflowVersion | None:
        rows = await self.list_for_workflow(workflow_id, org_id, limit=1)
        return rows[0] if rows else None

    async def delete(self, version_id: str, org_id: str) -> bool:
        return self.versions.pop(version_id, None) is not None


class FakeWorkflowBuilder:
    """`WorkflowBuilderAgent` without the LLM."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.calls: list[dict[str, Any]] = []

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        from app.services.workflows.ir.extractor import extract_ir

        return {"ok": True, "source": self.source, "ir": extract_ir(self.source).model_dump()}


class FakeMessagingProducer:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        self.sent.append({"topic": topic, "event_type": event_type, "payload": payload, "key": key})
        return True

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        return await self.send_event(topic, "message", message, key=key)


class FakeTaskNotifier:
    def __init__(self) -> None:
        self.notifications: list[Any] = []

    async def notify(self, notification: Any) -> None:
        self.notifications.append(notification)


class FakeConversationWriter:
    """Stands in for the Node internal route the executor posts results to."""

    def __init__(self) -> None:
        self.results: list[Any] = []

    async def append_result(self, conversation_id: str, org_id: str, message: Any) -> None:
        self.results.append(message)

    async def aclose(self) -> None:
        pass


class FakeSpecAssembler:
    """Only `build_context_and_tools` matters here — the code path never
    assembles an agent."""

    def __init__(self, registry: FakeToolRegistry) -> None:
        self._registry = registry

    async def build_context_and_tools(self, task: Any, **_kwargs: Any) -> tuple[Any, FakeToolRegistry, str]:
        return SimpleNamespace(), self._registry, ""

    async def assemble(self, task: Any, **_kwargs: Any):  # noqa: ANN201
        raise AssertionError("a code workflow must never fall through to the agent path")


@pytest.fixture
async def redis_client() -> fake_aioredis.FakeRedis:
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


class _Harness:
    def __init__(
        self,
        redis_client: fake_aioredis.FakeRedis,
        *,
        tools: dict[str, list[str]] | None = None,
    ) -> None:
        self.clock = FixedClock(datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc))
        self.graph_provider = FakeGraphProvider()
        self.task_store = GraphTaskStore(self.graph_provider)
        self.trigger_store = RedisTriggerStore(redis_client)
        self.run_store = RedisRunStore(redis_client)
        self.producer = FakeMessagingProducer()
        self.notifier = FakeTaskNotifier()
        self.journal = InMemoryJournal()
        self.code_store = FakeCodeStore()
        self.version_store = FakeVersionStore()
        self.registry = FakeToolRegistry(tools or {
            "jira__search_issues": ["read"],
            "slack__post_message": ["write"],
        })
        self.conversation_writer = FakeConversationWriter()

        self.engine = TaskEngine(
            task_store=self.task_store, trigger_store=self.trigger_store,
            run_store=self.run_store, producer=self.producer, clock=self.clock,
        )
        self.runner = CodeWorkflowRunner(
            journal=self.journal,
            broker=None,  # every run supplies its own; None would fail loudly
            version_store=self.version_store,
            code_store=self.code_store,
        )
        self.executor = TaskExecutor(
            task_store=self.task_store, run_store=self.run_store,
            checkpoint_store_factory=lambda _org: SimpleNamespace(latest=AsyncMock(return_value=None)),
            spec_assembler=FakeSpecAssembler(self.registry),
            graph_provider=self.graph_provider, config_service=None,
            producer=self.producer, notifier=self.notifier, clock=self.clock,
            owner="exec-e2e", heartbeat_interval_seconds=999.0,
            code_workflow_runner=self.runner, trigger_store=self.trigger_store,
            conversation_writer=self.conversation_writer,
        )

    def manage_tool(self, *, source: str | None = None) -> "WorkflowManageTool":
        from app.agents.agent_loop.tools.tasks.workflow_manage import WorkflowManageTool

        return WorkflowManageTool(
            self.engine, org_id=_ORG, user_id=_USER, user_email="u@example.com",
            graph_provider=self.graph_provider, config_service=None,
            conversation_id=_CONVERSATION,
            # The chat session's live registry, which is what creation-time
            # tool validation checks a requested name against.
            available_tool_names=self.registry.names,
            workflow_builder=FakeWorkflowBuilder(source or _GENERATED_SOURCE),
            code_store=self.code_store, version_store=self.version_store,
        )

    async def drain(self) -> None:
        """Let the executor's post-run background work finish. A retry backoff
        timer is one of those tasks and sleeps for minutes, so whatever is
        still pending after a moment is cancelled rather than awaited."""
        pending = list(self.executor._background_tasks)
        if not pending:
            return
        _, still_running = await asyncio.wait(pending, timeout=1.0)
        for background_task in still_running:
            background_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await background_task

    async def dispatch_latest(self) -> str:
        """Hand the newest dispatch event to the executor, as Kafka would."""
        events = [e for e in self.producer.sent if e["topic"] == Topic.TASK_EVENTS.value]
        payload = events[-1]["payload"]
        await self.executor.handle_dispatch(
            StreamMessage(eventType="task_run_dispatch", payload=payload),
        )
        await self.drain()
        return payload["run_id"]


class TestCodeWorkflowLifecycleE2E:
    async def test_chat_to_dry_run_to_trigger_fire(self, redis_client: fake_aioredis.FakeRedis) -> None:
        harness = _Harness(redis_client)
        fire_at = (harness.clock.now() + timedelta(hours=1)).isoformat()

        # -- Step 1: the user describes the workflow in chat. Codegen runs
        # inline, the source is versioned, and the task is pinned to it.
        created = await harness.manage_tool().execute(
            action="create",
            title="Daily digest",
            description="post my open issues to #general",
            instructions="Every morning, post my open Jira issues to #general",
            tool_names=["jira__search_issues", "slack__post_message"],
            triggers=[{"kind": "one_time", "fire_at": fire_at}],
        )
        assert created.success is True, created.error
        workflow_id = created.data["workflow_id"]
        assert created.data["execution_kind"] == "code"
        version_id = created.data["workflow_version_id"]

        stored = await harness.version_store.get(version_id, _ORG)
        assert stored is not None
        assert stored.version_number == 1
        source = await harness.code_store.get(stored.bundle_ref)
        assert hashlib.sha256(source).hexdigest() == stored.content_hash

        # The one-time trigger is armed rather than stored dead.
        trigger = created.data["triggers"][0]
        assert trigger["next_run_at"] == fire_at

        # -- Step 2: dry run. Reads execute so the user sees real data; the
        # write is skipped, and the card still gets its terminal update.
        dry_run = await harness.engine.dry_run(workflow_id, _ORG)
        await harness.dispatch_latest()

        assert harness.registry.tools["jira__search_issues"].calls != []
        assert harness.registry.tools["slack__post_message"].calls == []
        dry_notifications = [n for n in harness.notifier.notifications if n.run_id == dry_run.run_id]
        assert dry_notifications, "the in-chat dry-run card never resolves without a live update"
        assert all(n.is_dry_run for n in dry_notifications)

        # -- Step 3: the trigger fires. SchedulerLoop claims it and publishes
        # the dispatch event the executor consumes.
        harness.clock.set(datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc))
        scheduler = SchedulerLoop(
            trigger_store=harness.trigger_store, run_store=harness.run_store,
            producer=harness.producer, clock=harness.clock, owner="sched-e2e",
        )
        stats = await scheduler.tick()
        assert (stats.dispatched_runs, stats.errors) == (1, 0)

        # -- Step 4: the real run executes the generated code against real
        # tools, journals every call, and lands on a terminal status.
        run_id = await harness.dispatch_latest()

        final = await harness.run_store.get(run_id)
        assert final.status is RunStatus.SUCCEEDED
        assert harness.registry.tools["slack__post_message"].calls != []

        entries = await harness.journal.load(run_id)
        assert [e.step_key for e in entries] == [
            "ctx.tool:jira__search_issues#0",
            "ctx.tool:slack__post_message#0",
        ]
        assert all(e.outcome.value == "succeeded" for e in entries)

        # -- Step 5: the result reaches the user — a notification deep-linking
        # to the originating conversation, and a result card posted into it.
        success = [
            n for n in harness.notifier.notifications
            if n.run_id == run_id and n.kind.value == "run_succeeded"
        ]
        assert len(success) == 1
        assert success[0].redirect_link == f"/chat?conversationId={_CONVERSATION}"
        assert success[0].is_dry_run is False

        posted = [m for m in harness.conversation_writer.results if m.run_id == run_id]
        assert len(posted) == 1
        assert posted[0].status == "succeeded"
        assert posted[0].workflow_id == workflow_id

    async def test_a_tool_outside_the_grant_is_denied_at_the_broker(
        self, redis_client: fake_aioredis.FakeRedis,
    ) -> None:
        """The grant is derived from the workflow's declared tools, so code
        that reaches for anything else fails even though the registry holds
        real credentials for it."""
        harness = _Harness(redis_client)

        created = await harness.manage_tool().execute(
            action="create",
            title="Read only digest",
            description="read my open issues",
            instructions="Read my open Jira issues",
            tool_names=["jira__search_issues"],
        )
        assert created.success is True, created.error
        workflow_id = created.data["workflow_id"]

        await harness.engine.run_now(workflow_id, _ORG)
        run_id = await harness.dispatch_latest()

        final = await harness.run_store.get(run_id)
        assert final.status is not RunStatus.SUCCEEDED
        assert harness.registry.tools["slack__post_message"].calls == []


_WEB_RESEARCH_SOURCE = '''
@sdk.workflow(name="market_research")
async def market_research(ctx, trigger_payload):
    hits = await ctx.tool("dynamic__web_search", query="competitor pricing changes")
    return f"# Market research\\n\\nFound: {hits}"
'''


class TestWebResearchWorkflowE2E:
    """The regression this whole change came from: a task asking for web
    search silently ran with a dozen unrelated tools, because the headless
    context never carried a `web_search_config` and unresolvable names fell
    back to the whole registry."""

    async def test_a_scheduled_web_research_run_reaches_the_conversation(
        self, redis_client: fake_aioredis.FakeRedis,
    ) -> None:
        harness = _Harness(redis_client, tools={"dynamic__web_search": ["read"]})
        fire_at = (harness.clock.now() + timedelta(minutes=1)).isoformat()

        created = await harness.manage_tool(source=_WEB_RESEARCH_SOURCE).execute(
            action="create",
            title="Market research",
            description="track competitor pricing",
            instructions="Search the web for competitor pricing changes",
            tool_names=["dynamic__web_search"],
            triggers=[{"kind": "one_time", "fire_at": fire_at}],
        )
        assert created.success is True, created.error

        harness.clock.set(harness.clock.now() + timedelta(minutes=2))
        scheduler = SchedulerLoop(
            trigger_store=harness.trigger_store, run_store=harness.run_store,
            producer=harness.producer, clock=harness.clock, owner="sched-web",
        )
        assert (await scheduler.tick()).dispatched_runs == 1

        run_id = await harness.dispatch_latest()

        final = await harness.run_store.get(run_id)
        assert final.status is RunStatus.SUCCEEDED
        assert harness.registry.tools["dynamic__web_search"].calls != []

        # The conversation gets the run's markdown, which is what the chat
        # renders through the normal answer path rather than as a card.
        posted = [m for m in harness.conversation_writer.results if m.run_id == run_id]
        assert len(posted) == 1
        assert posted[0].output_summary.startswith("# Market research")


class TestUnknownToolIsRejectedAtCreation:
    async def test_a_workflow_naming_a_missing_tool_is_never_created(
        self, redis_client: fake_aioredis.FakeRedis,
    ) -> None:
        """Catching this at creation is the difference between a corrected
        typo in chat and a task that fires at 3am and fails."""
        harness = _Harness(redis_client)

        created = await harness.manage_tool().execute(
            action="create",
            title="Market research",
            description="track competitor pricing",
            instructions="Search the web for competitor pricing changes",
            tool_names=["web_search"],
        )

        assert created.success is False
        assert "web_search" in created.error

        from app.services.tasks.domain.models import TaskQuery

        assert (await harness.task_store.list(TaskQuery(org_id=_ORG))).items == []

    async def test_the_rejection_suggests_the_tool_the_user_meant(
        self, redis_client: fake_aioredis.FakeRedis,
    ) -> None:
        harness = _Harness(redis_client, tools={"dynamic__web_search": ["read"]})

        created = await harness.manage_tool().execute(
            action="create",
            title="Market research",
            description="track competitor pricing",
            instructions="Search the web",
            tool_names=["web_search"],
        )

        assert created.success is False
        assert "dynamic__web_search" in created.error


class TestDryRunLeavesNoTrace:
    async def test_a_dry_run_performs_no_write_and_stays_retry_safe(
        self, redis_client: fake_aioredis.FakeRedis,
    ) -> None:
        """A dry run exists to show the user what would happen. If a write
        escapes, the preview has already changed the world; if the run is
        marked as having written, a later crash is treated as unsafe to
        retry for a run that never touched anything."""
        harness = _Harness(redis_client)

        created = await harness.manage_tool().execute(
            action="create",
            title="Daily digest",
            description="post my open issues to #general",
            instructions="Every morning, post my open Jira issues to #general",
            tool_names=["jira__search_issues", "slack__post_message"],
        )
        assert created.success is True, created.error

        await harness.engine.dry_run(created.data["workflow_id"], _ORG)
        run_id = await harness.dispatch_latest()

        assert harness.registry.tools["slack__post_message"].calls == []
        final = await harness.run_store.get(run_id)
        assert final.is_dry_run is True
        assert final.had_write_side_effect is False


@pytest.mark.asyncio
async def test_ir_extraction_is_deterministic() -> None:
    """The graph the detail view draws must not shift between renders of the
    same code."""
    import json

    from app.services.workflows.ir.extractor import extract_ir

    ir1 = extract_ir(_GENERATED_SOURCE)
    ir2 = extract_ir(_GENERATED_SOURCE)
    assert json.dumps(ir1.model_dump(), sort_keys=True) == json.dumps(ir2.model_dump(), sort_keys=True)
    assert [n.kind.value for n in ir1.nodes] == ["workflow", "tool_call", "tool_call"]
    # Distinct nodes per call site, or clicking one graph node scrolls to the
    # wrong line.
    assert len({n.node_id for n in ir1.nodes}) == 3
