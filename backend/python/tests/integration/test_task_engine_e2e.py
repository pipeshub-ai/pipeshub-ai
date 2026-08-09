"""End-to-end integration test for the task engine (task engine plan Phase
10 checklist item 13: "E2E (chat -> missing prerequisite -> fix -> schedule
-> fire -> execute -> dashboard/list API)").

Wires the REAL application-layer classes exactly as the unit suites for
each of them already do -- `TaskEngine`, `GraphTaskStore`,
`RedisTriggerStore`/`RedisRunStore` (over fakeredis, same as
`test_scheduler_loop.py`/`test_executor.py`), `PrerequisiteValidator`,
`SchedulerLoop`, `TaskExecutor` -- and drives them through the two entry
points production traffic actually uses: the chat tools (`task_manage`,
`task_find`) and the REST dashboard route functions
(`app.api.routes.workflows`), called the same direct-handler-call way
`test_workflows_routes.py` does (no `TestClient`/DI container).

The only doubles are the ones every other test in this package already
uses for the identical reason: `FakeGraphProvider` (no real Arango/Neo4j)
extended with the two prerequisite-check calls, and a `FakeSpecAssembler`/
`FakeAgent` in place of a real LLM-backed `Agent` -- that wiring is proven
separately by `test_headless_execution.py`/`test_task_dag_loop.py`, so
faking it here keeps this test about the task engine's OWN plumbing
(prerequisite gate -> schedule -> claim -> dispatch -> execute -> read
paths), not the agent loop underneath it.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakeredis import aioredis as fake_aioredis

import app.api.routes.workflows as workflow_routes
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agents.agent_loop.tools.tasks.task_find import TaskFindTool
from app.agents.agent_loop.tools.tasks.task_manage import TaskManageTool
from app.services.messaging.config import StreamMessage, Topic
from app.services.tasks.adapters.graph.task_store import GraphTaskStore
from app.services.tasks.adapters.redis.run_store import RedisRunStore
from app.services.tasks.adapters.redis.trigger_store import RedisTriggerStore
from app.services.tasks.application.engine import TaskEngine
from app.services.tasks.application.prerequisites import PrerequisiteValidator
from app.services.tasks.interface.clock import FixedClock
from app.services.tasks.runtime.executor import TaskExecutor
from app.services.tasks.runtime.scheduler_loop import SchedulerLoop
from app.services.workflows.application.workflow_service import WorkflowService
from tests.unit.services.tasks.adapters.fakes import FakeGraphProvider


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

    async def notify(self, notification: object) -> None:
        self.notifications.append(notification)


class ConnectorAwareGraphProvider(FakeGraphProvider):
    """Adds the two `PrerequisiteValidator` calls on top of the generic node
    CRUD `GraphTaskStore` needs -- a real deployment's single
    `IGraphDBProvider` implementation offers both callers from the one
    object, so this fake mirrors that rather than needing two separate
    doubles wired to two different names."""

    def __init__(self) -> None:
        super().__init__()
        self.connector_instances: list[dict] = []

    async def get_user_connector_instances(self, **kwargs: object) -> list[dict]:
        return self.connector_instances

    async def get_user_kb_permission(self, kb_id: str, user_id: str) -> str | None:
        return None


class FakeAgent:
    def __init__(self, run_id: str, *, result: AgentResult) -> None:
        self.run_ctx = SimpleNamespace(run_id=run_id)
        self.runtime = SimpleNamespace(hooks=None)
        self._result = result

    async def run(self, goal: Goal) -> AgentResult:
        return self._result

    async def resume(self, checkpoint_id: str, hil_responses: dict[str, str] | None = None) -> AgentResult:
        return self._result


class FakeSpecAssembler:
    def __init__(self) -> None:
        self.next_agent: FakeAgent | None = None

    async def assemble(self, task: object, **_kwargs: object) -> tuple[FakeAgent, Goal]:
        assert self.next_agent is not None, "test must set spec_assembler.next_agent first"
        return self.next_agent, Goal(description=task.instructions)  # type: ignore[attr-defined]


def _request(*, user_id: str = "user-1", org_id: str = "org-1") -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": user_id, "orgId": org_id, "email": "a@b.com"}
    request.app.container = MagicMock()
    return request


@pytest.fixture
async def redis_client() -> fake_aioredis.FakeRedis:
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


class TestChatToTaskToExecutionE2E:
    async def test_full_lifecycle(self, redis_client: fake_aioredis.FakeRedis) -> None:
        graph_provider = ConnectorAwareGraphProvider()
        task_store = GraphTaskStore(graph_provider)
        trigger_store = RedisTriggerStore(redis_client)
        run_store = RedisRunStore(redis_client)
        producer = FakeMessagingProducer()
        notifier = FakeTaskNotifier()
        clock = FixedClock(datetime(2024, 1, 1, 8, 59, 0, tzinfo=timezone.utc))

        engine = TaskEngine(
            task_store=task_store, trigger_store=trigger_store, run_store=run_store,
            producer=producer, prerequisite_validator=PrerequisiteValidator(), clock=clock,
        )
        manage_tool = TaskManageTool(
            engine, org_id="org-1", user_id="user-1", user_email="a@b.com",
            graph_provider=graph_provider, config_service=None, conversation_id="conv-1",
        )
        find_tool = TaskFindTool(engine, org_id="org-1", user_id="user-1")

        create_kwargs = {
            "action": "create", "title": "Daily Slack digest", "description": "summarize #general",
            "instructions": "Summarize #general", "connector_ids": ["conn-slack"],
            "triggers": [{"kind": "cron", "cron_expression": "0 9 * * *"}],
        }

        # -- Step 1: chat create is blocked -- Slack connector not connected.
        blocked = await manage_tool.execute(**create_kwargs)
        assert blocked.success is False
        assert "conn-slack" in blocked.error
        assert "not connected" in blocked.error

        # -- Step 2: fix -- the connector is now connected + authenticated,
        # so the identical chat request now succeeds.
        graph_provider.connector_instances = [
            {"_key": "conn-slack", "isConfigured": True, "isAuthenticated": True},
        ]
        created = await manage_tool.execute(**create_kwargs)
        assert created.success is True
        task_id = created.data["task_id"]
        assert created.data["triggers"][0]["next_run_at"] == "2024-01-01T09:00:00+00:00"

        # -- Step 3: schedule fires -- SchedulerLoop claims the due trigger
        # and publishes a dispatch event, exactly as the real background
        # loop would once its own tick reaches this instant.
        clock.set(datetime(2024, 1, 1, 9, 0, 0, tzinfo=timezone.utc))
        scheduler = SchedulerLoop(
            trigger_store=trigger_store, run_store=run_store, producer=producer, clock=clock, owner="sched-e2e",
        )
        stats = await scheduler.tick()
        assert stats.dispatched_runs == 1
        assert stats.errors == 0
        dispatch_events = [e for e in producer.sent if e["topic"] == Topic.TASK_EVENTS.value]
        assert len(dispatch_events) == 1
        dispatch_payload = dispatch_events[0]["payload"]
        assert dispatch_payload["task_id"] == task_id
        run_id = dispatch_payload["run_id"]

        # -- Step 4: execute -- TaskExecutor claims and drives the run to
        # completion (the underlying `Agent` is faked; see module docstring).
        spec_assembler = FakeSpecAssembler()
        spec_assembler.next_agent = FakeAgent("agent-run-1", result=AgentResult(
            goal=Goal(description="x"), output="Posted digest to #general", success=True,
        ))
        executor = TaskExecutor(
            task_store=task_store, run_store=run_store,
            checkpoint_store_factory=lambda _org_id: SimpleNamespace(latest=AsyncMock(return_value=None)),
            spec_assembler=spec_assembler, graph_provider=graph_provider, config_service=None,
            producer=producer, notifier=notifier, clock=clock, owner="exec-e2e",
            heartbeat_interval_seconds=999.0,
        )
        handled = await executor.handle_dispatch(StreamMessage(eventType="task_run_dispatch", payload=dispatch_payload))
        assert handled is True
        for background_task in list(executor._background_tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await background_task
        assert any(n.kind.value == "run_succeeded" for n in notifier.notifications)

        # -- Step 5a: dashboard surfaced via the chat `task_find` tool -----
        found = await find_tool.execute(task_id=task_id, include_runs=True, include_triggers=True)
        assert found.success is True
        assert found.data["task"]["status"] == "active"
        assert found.data["runs"][0]["status"] == "succeeded"
        assert found.data["runs"][0]["output_summary"] == "Posted digest to #general"
        assert found.data["triggers"][0]["run_count"] == 1

        # -- Step 5b: dashboard surfaced via the REST routes, called the
        # same direct-handler-call way the route unit tests exercise them.
        service = WorkflowService(task_engine=engine)
        request = _request()
        list_response = await workflow_routes.list_workflows(
            request, limit=50, offset=0, status=None, all_users=False, q=None,
            conversation_id=None, service=service,
        )
        assert any(w["workflowId"] == task_id for w in list_response["workflows"])

        run_response = await workflow_routes.get_workflow_run(
            task_id, run_id, request, service=service,
        )
        assert run_response["status"] == "succeeded"
        assert run_response["outputSummary"] == "Posted digest to #general"
        assert run_response["completedAt"] is not None

        # The trace is empty rather than absent for a run with no journal --
        # the endpoint must still answer 200 with the run itself.
        trace_response = await workflow_routes.get_run_trace(
            task_id, run_id, request, service=service,
        )
        assert trace_response["run"]["runId"] == run_id
        assert trace_response["traceEntries"] == []
