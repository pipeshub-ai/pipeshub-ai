"""Builds an `AgentContext` and a "langchain" `TransportRegistry` for a
background, non-HTTP agent run -- scheduled tasks today, any future
headless caller (webhooks, workflow steps) tomorrow.

`AgentContext.from_chat_state()` (`app/agents/agent_loop/context.py`) is
built from a `ChatState` dict that `build_initial_state()` assembles out of
HTTP-request-shaped inputs (`ChatQuery`, `user_info`/`org_info` dicts from
the route's session). A scheduler tick has none of that -- it has a
`TaskPrincipal` (persisted identity) and a handful of already-resolved DI
services. `build_headless_context()` is the sibling constructor for that
case: same `AgentContext` type, same `tool_state` seeding
(`AgentContext.model_post_init`), zero dependency on `ChatQuery`/HTTP.

Deliberately has NO import of `app.services.tasks.domain` -- nothing here
knows what a "task" is, so this module stays reusable by any other future
headless caller. `runtime/spec_assembler.py` is the only file allowed to
bridge task domain models to this function's parameters (see that module's
docstring and `domain/models.py`'s module docstring for the same rule
stated from the domain side).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.transport.opik_tracing import (
    resolve_opik_gate,
    traced_transport_factory,
)
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.langchain_transport import LangChainTransport

if TYPE_CHECKING:
    from logging import Logger

    from langchain_core.language_models.chat_models import BaseChatModel

    from app.config.configuration_service import ConfigurationService
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = ["build_headless_context", "build_transport_registry"]


def build_headless_context(
    *,
    org_id: str,
    user_id: str,
    user_email: str,
    graph_provider: "IGraphDBProvider",
    config_service: "ConfigurationService",
    blob_store: "BlobStorage | None" = None,
    logger: "Logger | None" = None,
    llm: "BaseChatModel | None" = None,
    is_service_account: bool = False,
    agent_toolsets: list[dict[str, Any]] | None = None,
    tool_to_toolset_map: dict[str, str] | None = None,
    toolset_configs: dict[str, dict[str, Any]] | None = None,
    connector_configs: dict[str, Any] | None = None,
    apps: list[str] | None = None,
    kb: list[str] | None = None,
    agent_knowledge: list[dict[str, Any]] | None = None,
    agent_skills: list[str] | None = None,
    system_prompt: str | None = None,
    instructions: str | None = None,
    custom_instructions: str | None = None,
    timezone: str | None = None,
    conversation_id: str | None = None,
    essential_toolset_names: list[str] | None = None,
    web_search_config: dict[str, Any] | None = None,
) -> AgentContext:
    """Every field not accepted here (SSE `event_sink`, `has_ui_client`,
    `transcript_collector`, attachment blocks, ...) is a chat/streaming-only
    concern that stays at its class default (`None`/`False`/empty) --
    exactly what a background run needs: `PipesHubToolLoader.load()` and
    `PipesHubPromptBuilder` both already tolerate those defaults (a
    `has_ui_client=False` request is the existing "no streaming client"
    case, not a new code path)."""
    return AgentContext(
        org_id=org_id,
        user_id=user_id,
        user_email=user_email,
        is_service_account=is_service_account,
        retrieval_service=None,
        graph_provider=graph_provider,
        config_service=config_service,
        blob_store=blob_store,
        logger=logger,
        llm=llm,
        agent_toolsets=agent_toolsets or [],
        tool_to_toolset_map=tool_to_toolset_map or {},
        toolset_configs=toolset_configs or {},
        has_knowledge=bool(apps or kb or agent_knowledge),
        apps=apps,
        kb=kb,
        agent_knowledge=agent_knowledge,
        agent_skills=agent_skills,
        connector_configs=connector_configs,
        system_prompt=system_prompt,
        instructions=instructions,
        custom_instructions=custom_instructions,
        timezone=timezone,
        conversation_id=conversation_id,
        essential_toolset_names=essential_toolset_names or [],
        # `PipesHubToolLoader._build_dynamic_tools` builds `web_search`/
        # `fetch_url` only when this is truthy, so a headless caller that
        # leaves it None can never search the web no matter what tools the
        # task declares.
        web_search_config=web_search_config,
        has_ui_client=False,
        event_sink=None,
        protocol="legacy",
    )


def build_transport_registry(
    llm: "BaseChatModel",
    *,
    model_name: str = "",
    model_key: str | None = None,
    opik_project_name: str | None = None,
) -> TransportRegistry:
    """Same "langchain" provider wiring `PipesHubAgentFactory.create()` uses
    (`factory.py`'s `transport_registry.register("langchain", ...)` call) --
    kept identical, including Opik tracing, so a scheduled run's LLM calls
    are traced the same way a chat run's are, not a second, divergent path."""
    opik_active = resolve_opik_gate(True)
    registry = TransportRegistry()
    registry.register(
        "langchain",
        traced_transport_factory(
            lambda: LangChainTransport(
                llm, model_name=model_name, opik_project_name=opik_project_name, model_key=model_key,
            ),
            opik_active=opik_active,
            project_name=opik_project_name,
        ),
    )
    return registry
