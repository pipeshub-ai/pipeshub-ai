"""Starts/stops the task engine's `SchedulerLoop` + `TaskExecutor` (+ the
`Topic.TASK_EVENTS` consumer that feeds the executor) inside the Query
service process (Phase 9 of the task engine plan, Part H: "register
stores, scheduler, executor"; Part K1: "Scheduler and executor run
in-process in Query service as asyncio background tasks").

Deliberately NOT wired as `providers.Resource` entries in
`app.containers.query.QueryAppContainer` -- unlike every existing Resource
in this codebase (`graph_provider`, `blob_store`, `vector_db_service`, ...),
`SchedulerLoop`/`TaskExecutor` need an explicit `.stop()` call before
process exit, and nothing in `query_main.py`'s shutdown path calls
`container.shutdown_resources()` today (consumers/producers are stopped by
hand: see `stop_kafka_consumers`/`stop_messaging_producer` in
`connectors_main.py`, `stop_kafka_consumers` in `query_main.py`).
`start_task_engine`/`stop_task_engine` are the SAME hand-called
start/stop-pair convention, invoked from `query_main.py`'s `lifespan`,
rather than a second, differently-behaved lifecycle mechanism this
codebase doesn't otherwise exercise.

Best-effort like `tasks_wiring.build_task_engine` (the chat-tool wiring):
`start_task_engine` returns `None` -- never raises -- if the feature is
disabled or any dependency fails to initialize, so a broken/unreachable
task-engine Redis can never take down Query's other responsibilities
(search, chat) at startup.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.modules.stores.checkpoint.graph_store import (
    GraphCheckpointStore,
)
from app.agent_loop_lib.modules.stores.timeline.graph_store import GraphTimelineStore
from app.agents.agent_loop.tasks_wiring import tasks_enabled
from app.connectors.services.kafka_service import KafkaService
from app.services.agents.provisioning import AgentProvisioningService
from app.services.events.consumer import AppEventConsumer
from app.services.messaging.config import Topic
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils
from app.services.notification.notification_service import NotificationService
from app.services.tasks.adapters.messaging.notifier import MessagingNotifier
from app.services.tasks.application.engine import TaskEngine
from app.services.tasks.application.prerequisites import PrerequisiteValidator
from app.services.tasks.runtime.executor import TaskExecutor
from app.services.tasks.runtime.scheduler_loop import SchedulerLoop
from app.services.tasks.runtime.spec_assembler import TaskSpecAssembler
from app.services.tasks.task_store_provider_factory import (
    TaskScheduleStoreFactory,
    TaskStoreProviderFactory,
)
from app.services.workflows.adapters.graph import (
    GraphWorkflowCodeStore,
    GraphWorkflowVersionStore,
)
from app.services.workflows.adapters.node.conversation_writer import (
    NodeConversationWriter,
    build_node_conversation_writer,
)
from app.services.workflows.adapters.redis.journal import RedisExecutionJournal
from app.services.workflows.adapters.redis.state_store import RedisWorkflowStateStore
from app.services.workflows.runtime.agent_runner import WorkflowAgentRunner
from app.services.workflows.runtime.broker import build_platform_broker
from app.services.workflows.runtime.code_runner import CodeWorkflowRunner
from app.services.workflows.runtime.sandbox import SubprocessSandboxProvisioner

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from logging import Logger

    from redis.asyncio import Redis

    from app.containers.query import QueryAppContainer
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.interface.consumer import IMessagingConsumer
    from app.services.messaging.interface.producer import IMessagingProducer

__all__ = ["TaskEngineRuntime", "start_task_engine", "stop_task_engine"]

_CODE_SANDBOX_ENV = "WORKFLOW_CODE_SANDBOX_TRUSTED_TENANTS"
"""Deployment's assertion that everyone who can author a workflow is trusted.

`SubprocessSandboxProvisioner` gives the child restricted builtins, a stripped
env and rlimits, but no filesystem or network isolation -- it runs as the
service user and can dial localhost. That is a property of the provisioner, not
of any code we can write above it, so the decision to accept it belongs to
whoever deploys rather than to a default buried in the wiring. Defaults on to
preserve existing deployments; set to 0 to refuse code-mode entirely until a
container/gVisor/E2B provisioner implements `ISandboxSessionProvisioner`.
"""

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class _CodeSandboxDisabled(Exception):
    """Operator turned code-mode off -- not a wiring failure."""


def _code_sandbox_enabled() -> bool:
    return os.getenv(_CODE_SANDBOX_ENV, "1").strip().lower() in _TRUTHY


def _make_journal_payload_store_factory(
    *, graph_provider: object, blob_store: object, logger: "Logger",
) -> "Callable[[Any], Any]":
    """Per-run `IJournalPayloadStore`, so oversized step results go to object
    storage instead of sitting in Redis for the journal's whole TTL.

    A factory rather than a store because artifacts are written as the run's
    own tenant; returning None simply leaves that run's results inline.
    """
    def _build(principal: "Any") -> "Any":
        if graph_provider is None or blob_store is None:
            return None
        from app.services.workflows.adapters.artifact import ArtifactJournalPayloadStore

        try:
            return ArtifactJournalPayloadStore(
                graph_provider=graph_provider,
                blob_store=blob_store,
                org_id=principal.org_id,
                user_id=principal.user_id,
                conversation_id=principal.conversation_id,
            )
        except Exception:
            logger.exception("tasks: could not build journal payload store for run")
            return None

    return _build


_TASK_CONSUMER_CLIENT_ID = "task_executor_client"
_TASK_CONSUMER_GROUP_ID = "task_executor_group"
_TASK_PRODUCER_CLIENT_ID = "task_engine_producer"
_APP_EVENT_CONSUMER_CLIENT_ID = "app_event_consumer_client"
_APP_EVENT_CONSUMER_GROUP_ID = "app_event_consumer_group"


@dataclass
class TaskEngineRuntime:
    """Every resource `start_task_engine` created, in creation order, so
    `stop_task_engine` can unwind it in reverse -- consumer first (stop
    accepting new dispatches), then the two loops, then the transport they
    both publish/notify through.

    Every field but `redis_client` is `None`-able: `start_task_engine`
    passes a partially-populated instance to `stop_task_engine` when a
    LATER step fails after an EARLIER one already started (e.g. the
    scheduler started fine but building the executor raised) -- see that
    function's own except-block."""

    redis_client: "Redis"
    producer: "IMessagingProducer | None"
    scheduler: SchedulerLoop | None
    executor: TaskExecutor | None
    consumer: "IMessagingConsumer | None"
    event_consumer: "IMessagingConsumer | None" = None


async def start_task_engine(
    app_container: "QueryAppContainer", *, graph_provider: "IGraphDBProvider", logger: "Logger",
) -> TaskEngineRuntime | None:
    """`None` when `PIPESHUB_ENABLE_TASKS=false` (see `tasks_enabled()`) or
    when any dependency fails to initialize -- callers must treat a `None`
    return as "task scheduling/execution is disabled for this process",
    never as a fatal startup error. Whatever got far enough to actually
    start (e.g. the scheduler loop, if the executor's own setup fails
    afterward) is torn down before returning, so a partial failure never
    leaks a running background loop nobody holds a reference to."""
    if not tasks_enabled():
        logger.info("tasks: PIPESHUB_ENABLE_TASKS=false -- scheduler/executor not started")
        return None

    redis_client: "Redis | None" = None
    producer: "IMessagingProducer | None" = None
    scheduler: SchedulerLoop | None = None
    executor: TaskExecutor | None = None
    consumer: "IMessagingConsumer | None" = None
    event_consumer: "IMessagingConsumer | None" = None
    try:
        config_service = app_container.config_service()
        redis_config = await config_service.get_redis_config()
        redis_client = await TaskScheduleStoreFactory.create_redis_client(redis_config)
        task_store = TaskStoreProviderFactory.create_provider(graph_provider)
        trigger_store = await TaskScheduleStoreFactory.create_trigger_store(
            logger, redis_config, redis_client=redis_client,
        )
        run_store = await TaskScheduleStoreFactory.create_run_store(
            logger, redis_config, redis_client=redis_client, graph_provider=graph_provider,
        )

        producer_config = await MessagingUtils.create_producer_config_from_service(
            config_service, client_id=_TASK_PRODUCER_CLIENT_ID,
        )
        producer = MessagingFactory.create_producer(
            logging.getLogger("app.services.tasks.producer"), producer_config,
        )
        await producer.initialize()

        # `KafkaService`/`NotificationService` are the existing connector-
        # notification pipeline (see `MessagingNotifier`'s own docstring on
        # why this engine reuses it rather than publishing directly) --
        # "kafka" in both names is legacy; each already delegates to
        # whichever `IMessagingProducer` it's given.
        notifier = MessagingNotifier(
            NotificationService(KafkaService(config_service, logger, producer=producer), logger),
        )

        blob_store = await app_container.blob_store()

        scheduler = SchedulerLoop(
            trigger_store=trigger_store, run_store=run_store, producer=producer, logger=logger,
        )
        scheduler.start()

        # Build code workflow runner (journal + provisioner).
        # The broker is per-run and must include the run's ToolRegistry, so we
        # pass broker=None here and let TaskExecutor supply it via the broker=
        # keyword on each call.  Best-effort: a failure here keeps the executor
        # alive with code_workflow_runner=None so agent-task runs still work.
        code_workflow_runner: CodeWorkflowRunner | None = None
        workflow_state_store: RedisWorkflowStateStore | None = None
        try:
            if not _code_sandbox_enabled():
                raise _CodeSandboxDisabled
            workflow_state_store = RedisWorkflowStateStore(redis_client)
            _version_store = GraphWorkflowVersionStore(graph_provider)
            _code_store = GraphWorkflowCodeStore(graph_provider)
            _journal = RedisExecutionJournal(redis_client)
            _payload_store_factory = _make_journal_payload_store_factory(
                graph_provider=graph_provider, blob_store=blob_store, logger=logger,
            )
            _provisioner = SubprocessSandboxProvisioner()
            # Fallback broker (no tool registry) used only when the executor
            # does not supply a per-run broker override — should not happen in
            # production but prevents AttributeError on None.
            _fallback_broker = build_platform_broker(tool_registry=None)
            code_workflow_runner = CodeWorkflowRunner(
                journal=_journal,
                broker=_fallback_broker,
                version_store=_version_store,
                code_store=_code_store,
                provisioner=_provisioner,
                payload_store_factory=_payload_store_factory,
            )
            logger.info(
                "✅ CodeWorkflowRunner wired (subprocess sandbox + RedisJournal). "
                "%s=1: workflow code runs as the service user with network access; "
                "this deployment is asserting all tenants that can author workflows "
                "are trusted.",
                _CODE_SANDBOX_ENV,
            )
        except _CodeSandboxDisabled:
            logger.warning(
                "tasks: code workflows disabled (%s=0). Agent-mode tasks still run; "
                "code-mode workflow runs will fail their prerequisite check.",
                _CODE_SANDBOX_ENV,
            )
        except Exception:
            logger.exception("tasks: failed to build CodeWorkflowRunner — code workflows disabled")

        # Build NodeConversationWriter so completed runs can post results back
        # to the originating chat conversation.  Best-effort: failures here
        # must not prevent the executor from starting.
        conversation_writer: NodeConversationWriter | None = None
        try:
            conversation_writer = await build_node_conversation_writer(config_service)
            if conversation_writer is None:
                logger.warning("tasks: scopedJwtSecret not found — NodeConversationWriter disabled")
            else:
                logger.info("✅ NodeConversationWriter wired")
        except Exception:
            logger.exception("tasks: failed to build NodeConversationWriter — run-result write-back disabled")

        agent_runner: WorkflowAgentRunner | None = None
        try:
            agent_runner = WorkflowAgentRunner(
                graph_provider=graph_provider,
                config_service=config_service,
                blob_store=blob_store,
            )
            logger.info("✅ WorkflowAgentRunner wired — ctx.agent().run() is live")
        except Exception:
            logger.exception("tasks: failed to build WorkflowAgentRunner — ctx.agent() disabled")

        executor = TaskExecutor(
            task_store=task_store,
            run_store=run_store,
            checkpoint_store_factory=lambda org_id: GraphCheckpointStore(graph_provider, org_id=org_id),
            timeline_store_factory=lambda org_id: GraphTimelineStore(graph_provider, org_id=org_id),
            spec_assembler=TaskSpecAssembler(),
            graph_provider=graph_provider,
            config_service=config_service,
            producer=producer,
            notifier=notifier,
            prerequisite_validator=PrerequisiteValidator(),
            blob_store=blob_store,
            trigger_store=trigger_store,
            code_workflow_runner=code_workflow_runner,
            conversation_writer=conversation_writer,
            workflow_state_store=workflow_state_store,
            agent_provisioning_service=AgentProvisioningService(graph_provider, config_service),
            agent_runner=agent_runner,
            logger=logger,
        )
        executor.start_reaper()

        retry_manager = MessagingFactory.create_retry_manager(logger, redis_config)
        await retry_manager.initialize()
        consumer_config = await MessagingUtils.create_consumer_config(
            app_container, _TASK_CONSUMER_CLIENT_ID, _TASK_CONSUMER_GROUP_ID, [Topic.TASK_EVENTS.value],
        )
        consumer = MessagingFactory.create_consumer(logger, consumer_config, retry_manager=retry_manager)
        await consumer.start(executor.handle_dispatch)

        # Connector webhooks land on APP_EVENTS via the ingress in the
        # connectors service; without a consumer here nothing ever matches
        # them against event triggers, so `@workflow(triggers=[on_event(...)])`
        # would store fine and never fire. Best-effort: a failure leaves
        # scheduled workflows working.
        try:
            event_engine = TaskEngine(
                task_store=task_store,
                trigger_store=trigger_store,
                run_store=run_store,
                producer=producer,
                prerequisite_validator=PrerequisiteValidator(),
                logger=logger,
            )
            event_consumer_config = await MessagingUtils.create_consumer_config(
                app_container,
                _APP_EVENT_CONSUMER_CLIENT_ID,
                _APP_EVENT_CONSUMER_GROUP_ID,
                [Topic.APP_EVENTS.value],
            )
            event_consumer = MessagingFactory.create_consumer(
                logger, event_consumer_config, retry_manager=retry_manager,
            )
            await event_consumer.start(AppEventConsumer(task_engine=event_engine).handle)
            logger.info("✅ App-event consumer started — event triggers are live")
        except Exception:
            logger.exception(
                "tasks: failed to start app-event consumer — event-triggered workflows disabled",
            )
    except Exception:
        logger.exception("tasks: failed to start scheduler/executor -- task scheduling disabled for this process")
        await stop_task_engine(
            TaskEngineRuntime(
                redis_client=redis_client, producer=producer, scheduler=scheduler,
                executor=executor, consumer=consumer, event_consumer=event_consumer,
            ) if redis_client is not None else None,
            logger=logger,
        )
        return None

    logger.info("✅ Task engine scheduler + executor started")
    return TaskEngineRuntime(
        redis_client=redis_client, producer=producer, scheduler=scheduler, executor=executor,
        consumer=consumer, event_consumer=event_consumer,
    )


async def _stop_step(name: str, coro: "Coroutine[Any, Any, None]", *, logger: "Logger") -> None:
    try:
        await coro
    except Exception:
        logger.exception("tasks: error stopping %s during task engine shutdown", name)


async def stop_task_engine(runtime: TaskEngineRuntime | None, *, logger: "Logger") -> None:
    """Tolerant of a partially-populated `runtime` (any field may be `None`
    -- see `start_task_engine`'s own unwind-on-failure call) and of any
    individual step failing; every step is attempted regardless of whether
    an earlier one raised, so one stuck component can never prevent the
    others from shutting down."""
    if runtime is None:
        return
    if runtime.event_consumer is not None:
        await _stop_step("app_events consumer", runtime.event_consumer.stop(), logger=logger)
    if runtime.consumer is not None:
        await _stop_step("task_events consumer", runtime.consumer.stop(), logger=logger)
    if runtime.executor is not None:
        await _stop_step("task executor", runtime.executor.stop(), logger=logger)
    if runtime.scheduler is not None:
        await _stop_step("scheduler loop", runtime.scheduler.stop(), logger=logger)
    if runtime.producer is not None:
        await _stop_step("task producer", runtime.producer.cleanup(), logger=logger)
    if runtime.redis_client is not None:
        await _stop_step("task redis client", runtime.redis_client.aclose(), logger=logger)
    logger.info("Task engine scheduler/executor stopped")
