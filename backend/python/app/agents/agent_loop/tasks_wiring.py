"""Wires workflow tools (``workflow_find``, ``workflow_manage``,
``sdk_reference``) into ``PipesHubAgentFactory.create()``.

Only the **workflow** vocabulary is exposed to the agent. The ``task_find``
/ ``task_manage`` tools are deliberately *not* registered: having two
overlapping tool sets confused the model — it would pick ``task_manage``
(which had no codegen wiring) instead of ``workflow_manage``, silently
producing agent-task-only workflows with no generated code and no error.
One clear tool group eliminates the ambiguity.

Deliberately does NOT depend on the DI container
(``app.containers.query.QueryAppContainer``). ``ITaskStore`` is built
fresh per call (thin wrapper over the already-connected
``context.graph_provider``); the Redis client and messaging producer are
process-level singletons (module-global, guarded by a lock).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from app.agent_loop_lib.tools.errors import (
    DuplicateToolNameError,
    DuplicateToolPathError,
)
from app.agents.agent_loop.tools.tasks.workflow_find import WorkflowFindTool
from app.agents.agent_loop.tools.tasks.workflow_manage import WorkflowManageTool
from app.services.workflows.codegen.agent import WorkflowBuilderAgent
from app.services.workflows.codegen.sdk_reference_tool import SdkReferenceTool
from app.services.workflows.adapters.graph import (
    GraphWorkflowCodeStore,
    GraphWorkflowVersionStore,
)
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils
from app.services.tasks.adapters.config.webhook_secret_store import (
    ConfigServiceWebhookSecretStore,
)
from app.services.tasks.adapters.graph.task_store import GraphTaskStore
from app.services.tasks.application.engine import TaskEngine
from app.services.tasks.application.prerequisites import PrerequisiteValidator
from app.services.tasks.task_store_provider_factory import TaskScheduleStoreFactory

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext
    from app.config.configuration_service import ConfigurationService
    from app.services.messaging.interface.producer import IMessagingProducer

__all__ = [
    "tasks_enabled",
    "build_task_engine",
    "register_task_tools",
    "shared_task_redis_client",
    "shared_task_producer",
    "TASK_TOOL_NAMES",
]

logger = logging.getLogger(__name__)

TASK_TOOL_NAMES = ("workflow_find", "workflow_manage", "sdk_reference")

_process_init_lock = asyncio.Lock()
_shared_redis_client = None
_shared_producer: "IMessagingProducer | None" = None


def tasks_enabled() -> bool:
    """Kill-switch for the whole task-engine chat surface -- deployments
    whose Redis has no AOF/RDB persistence configured, or that simply
    haven't opted into the feature yet, can turn it off without a code
    change (see the plan's Part L open risk on Redis persistence)."""
    return os.getenv("PIPESHUB_ENABLE_TASKS", "true").strip().lower() == "true"


async def shared_task_redis_client(config_service: "ConfigurationService") -> "Redis":
    """Process-level singleton Redis connection for the task engine's
    `ITriggerStore`/`ITaskRunStore` -- shared by every caller that needs a
    cheap, request/turn-scoped `TaskEngine` (chat tools via
    `build_task_engine` below, and the REST routes in
    `app/api/routes/workflows.py`), so neither path opens a redundant
    connection for the exact same purpose."""
    global _shared_redis_client
    if _shared_redis_client is not None:
        return _shared_redis_client
    async with _process_init_lock:
        if _shared_redis_client is None:
            redis_config = await config_service.get_redis_config()
            _shared_redis_client = await TaskScheduleStoreFactory.create_redis_client(redis_config)
    return _shared_redis_client


async def shared_task_producer(config_service: "ConfigurationService") -> "IMessagingProducer":
    """Process-level singleton producer for `run_now`/`cancel`-style
    dispatches issued from outside the scheduler/executor's own producer
    (see `shared_task_redis_client`'s docstring for the sharing rationale)."""
    global _shared_producer
    if _shared_producer is not None:
        return _shared_producer
    async with _process_init_lock:
        if _shared_producer is None:
            config = await MessagingUtils.create_producer_config_from_service(
                config_service, client_id="task_engine_chat_producer",
            )
            # Lazily connects on first `send_event()` call (see
            # `RedisStreamsProducer.send_message`/`KafkaMessagingProducer`'s
            # own `initialize()`-on-first-use guard) -- no explicit
            # `initialize()`/`start()` needed here.
            _shared_producer = MessagingFactory.create_producer(
                logging.getLogger("app.services.tasks.chat_producer"), config,
            )
    return _shared_producer


async def build_task_engine(context: "AgentContext") -> TaskEngine | None:
    """Return ``None`` when graph provider, config service, or Redis is
    unavailable — callers skip tool registration rather than binding tools
    to a broken engine."""
    if context.graph_provider is None or context.config_service is None:
        logger.warning(
            "tasks: no graph_provider/config_service on this request's context -- "
            "workflow tools will not be available this turn"
        )
        return None

    try:
        task_store = GraphTaskStore(context.graph_provider)
        redis_client = await shared_task_redis_client(context.config_service)
        redis_config = await context.config_service.get_redis_config()
        trigger_store = await TaskScheduleStoreFactory.create_trigger_store(
            logger, redis_config, redis_client=redis_client,
        )
        run_store = await TaskScheduleStoreFactory.create_run_store(
            logger, redis_config, redis_client=redis_client,
            graph_provider=context.graph_provider,
        )
        producer = await shared_task_producer(context.config_service)
    except Exception:
        logger.exception(
            "tasks: failed to build task engine dependencies -- "
            "workflow tools will not be available this turn"
        )
        return None

    return TaskEngine(
        task_store=task_store,
        trigger_store=trigger_store,
        run_store=run_store,
        producer=producer,
        prerequisite_validator=PrerequisiteValidator(),
        webhook_secret_store=ConfigServiceWebhookSecretStore(context.config_service),
        logger=logger,
    )


def _make_llm_caller(context: "AgentContext"):  # type: ignore[return]
    """Build an async callable `(prompt, system_prompt) -> str` backed by the
    same LangChain model the chat turn is already using. Returns None if the
    model is not configured (tests / background contexts)."""
    llm = getattr(context, "llm", None)
    if llm is None:
        return None

    async def _call(prompt: str, system_prompt: str) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=prompt)]
        response = await llm.ainvoke(messages)
        content = response.content if hasattr(response, "content") else str(response)
        if isinstance(content, list):
            content = "\n".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        return content

    return _call


def register_task_tools(tool_registry: "ToolRegistry", engine: TaskEngine, context: "AgentContext") -> None:
    """Register ``workflow_find``, ``workflow_manage``, and ``sdk_reference``
    into the agent's tool registry under a ``"tasks"`` toolset group.

    Only the *workflow* vocabulary is exposed. ``task_find``/``task_manage``
    are intentionally excluded — both tools call the same ``TaskEngine``
    underneath, but only ``workflow_manage`` has codegen wired in. Exposing
    both confused the model (it would pick ``task_manage``, silently skipping
    code generation). One clear tool set eliminates the ambiguity."""
    workflow_builder: WorkflowBuilderAgent | None = None
    code_store: GraphWorkflowCodeStore | None = None
    version_store: GraphWorkflowVersionStore | None = None
    if context.graph_provider is None:
        logger.warning(
            "tasks_wiring: no graph_provider on this request's context -- "
            "codegen deps (code_store/version_store/workflow_builder) will not "
            "be built; workflow_manage will run in agent-task-only mode this turn"
        )
    else:
        try:
            code_store = GraphWorkflowCodeStore(context.graph_provider)
            version_store = GraphWorkflowVersionStore(context.graph_provider)
            llm_caller = _make_llm_caller(context)
            if llm_caller is None:
                logger.warning(
                    "tasks_wiring: no LLM configured on this request's context -- "
                    "workflow_builder will not be built; code/version stores are "
                    "ready but workflow_manage will skip codegen and run in "
                    "agent-task-only mode this turn"
                )
            else:
                workflow_builder = WorkflowBuilderAgent(llm_caller=llm_caller)
        except Exception:
            logger.exception(
                "tasks_wiring: failed to build codegen deps — "
                "workflow_manage will run in agent-task mode"
            )

    session_toolset_ids = [
        ts.get("instanceId") or ts.get("_id", "")
        for ts in (context.agent_toolsets or [])
        if ts.get("instanceId") or ts.get("_id")
    ]

    try:
        tool_registry.register_tool(WorkflowFindTool(engine, org_id=context.org_id, user_id=context.user_id))
        tool_registry.register_tool(WorkflowManageTool(
            engine,
            org_id=context.org_id,
            user_id=context.user_id,
            user_email=context.user_email,
            graph_provider=context.graph_provider,
            config_service=context.config_service,
            conversation_id=context.conversation_id,
            session_toolset_ids=session_toolset_ids,
            available_tool_names=tool_registry.names,
            workflow_builder=workflow_builder,
            code_store=code_store,
            version_store=version_store,
        ))
        tool_registry.register_tool(SdkReferenceTool())
    except (DuplicateToolNameError, DuplicateToolPathError):
        logger.exception("tasks_wiring: tool name/path collision -- workflow tools not registered")
        return
    tool_registry.register_toolset(
        "tasks", "Create, find, and manage scheduled/recurring workflows.", list(TASK_TOOL_NAMES),
    )
