"""The built-in assistant (no agent) on the durable path (P1.5b).

Legacy `/chat/stream` builds a turn with `chat_modes.setup` from the request;
a durable run builds the same turn from what it pinned: the chat grant
(`x.pipeshub.chat`: mode, knowledge on or off, the web tool grant), the
knowledge scope and the admitted input. The route's own lookups (the user and
org documents, the user-context setting, the org's custom instructions) are
made here the way `chatbot._generate_chat_stream_via_agent_loop` makes them.

The mode's upfront retrieval is part of the goal pre-run step (`ChatGoal`), so
it runs once per run and is journaled with the goal and the citation sources
it added; a resumed attempt reads both back instead of searching again.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harness_core.driver.refusal import RunRefused

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import Goal
    from app.agents.agent_loop.context import AgentContext
    from app.agents.chat_modes.policy import ChatModePolicy
    from app.agents.chat_modes.prefetch import PrefetchResult
    from app.agents.chat_modes.setup import ChatSetup
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.model import ResolvedModel
    from app.harness.assembly.services import RunServices
    from app.harness.assembly.tool_environment import ToolEnvironment
    from harness_core.logs import HarnessLog
    from harness_core.tools.registry import ToolRegistry


@dataclass
class ChatTurn:
    """What the goal step adds for a built-in assistant run."""

    setup: ChatSetup
    policy: ChatModePolicy
    retrieval_service: Any
    graph_provider: Any
    is_multimodal: bool


async def build_chat_environment(
    run: RunContext, services: RunServices, model: ResolvedModel
) -> ToolEnvironment:
    from app.agents.agent_loop.context import AgentContext
    from app.agents.chat_modes.setup import (
        TurnScopeRefused,
        enrich_chat_identity,
        prepare_chat_state,
    )
    from app.api.routes.chatbot import (
        DEFAULT_CONTEXT_LENGTH,
        _load_org_doc,
        _load_user_doc,
        load_system_prompts,
    )
    from app.harness.assembly.scope import requested_filters
    from app.harness.assembly.tool_environment import ToolEnvironment, _attachment, _log
    from app.services.featureflag.platform_settings import is_user_context_enabled
    from app.utils.attachment_utils import keep_accessible_attachments

    assert run.chat is not None
    logger = services.logger()
    config_service = services.config_service()
    graph = await services.graph_provider()
    retrieval = await services.retrieval_service()
    policy = run.chat.policy()

    user_doc, org_doc, user_context_flag, system_prompts, entity_store = await asyncio.gather(
        _load_user_doc(graph, run.actor_id),
        _load_org_doc(graph, run.org_id),
        is_user_context_enabled(config_service),
        load_system_prompts(config_service, logger),
        services.entity_vector_store(run.org_id),
        return_exceptions=True,
    )
    email = user_doc.get("email") if isinstance(user_doc, dict) else None
    user_info: dict[str, Any] = {
        "userId": run.actor_id,
        "orgId": run.org_id,
        "userEmail": email or "",
        "sendUserInfo": True,
    }
    org_info = enrich_chat_identity(user_info, run.org_id, user_doc, org_doc, user_context_flag, logger)
    # The client's recordId is never trusted: each kept attachment names the record the check found (#4255).
    attachments, previous_conversations = await keep_accessible_attachments(
        graph,
        org_id=run.org_id,
        user_id=run.actor_id,
        is_service_account=False,
        attachments=[_attachment(a) for a in run.attachments],
        previous_conversations=[dict(turn) for turn in run.previous_conversations],
        logger=logger,
    )
    query_info: dict[str, Any] = {
        "query": run.query,
        "limit": 50,
        "previous_conversations": previous_conversations,
        "filters": requested_filters(run.knowledge),
        "retrievalMode": "HYBRID",
        "quickMode": False,
        "chatMode": run.chat_mode,
        "timezone": run.timezone,
        "currentTime": run.prompt_time,
        "locale": run.locale,
        "conversationId": run.session_id,
        "projectInstructions": None,
        "attachments": attachments,
        "enableRecordIdShortening": False,
        "runId": run.run_id,
        "includeRetrievalContext": run.include_retrieval_context,
        "keywordSearchBudget": run.keyword_search_budget,
    }
    try:
        setup = await prepare_chat_state(
            query_info,
            user_info,
            model.chat_model(),
            policy,
            logger,
            retrieval_service=retrieval,
            graph_provider=graph,
            reranker_service=None,
            config_service=config_service,
            org_info=org_info,
            model_name=run.model.model_name,
            model_key=run.model.model_key,
            is_multimodal_llm=model.is_multimodal,
            system_prompts_config=system_prompts if isinstance(system_prompts, dict) else {},
            client_name=None,
            entity_vector_store=None if isinstance(entity_store, BaseException) else entity_store,
            org_id=run.org_id,
        )
    except TurnScopeRefused as refused:
        raise RunRefused(refused.code, refused.message) from refused
    # A durable run answers a question whatever its surface: it ends on needs_input and the reply resumes it.
    setup.chat_state["has_ui_client"] = True
    context = AgentContext.from_chat_state(
        setup.chat_state,
        protocol="agui",
        llm_provider=model.provider,
        context_length=model.context_length or DEFAULT_CONTEXT_LENGTH,
        # The model's own contextLength, unset when not configured: it sizes the context budget (main #4083).
        configured_context_window=model.context_length,
        run_id=run.run_id,
    )
    _log(run).info(
        "run.chat_environment",
        mode=policy.name,
        knowledge=policy.has_knowledge,
        webSearch=context.tool_state.get("web_search_config") is not None,
        prefetch=policy.prefetch_retrieval,
        attachments=len(run.attachments),
        customInstructions=bool(setup.chat_state.get("custom_instructions")),
    )
    return ToolEnvironment(
        context=context,
        agent=None,
        web_search_provider=None,
        chat=ChatTurn(setup, policy, retrieval, graph, model.is_multimodal),
    )


async def shape_chat_goal(
    turn: ChatTurn, context: AgentContext, goal: Goal, registry: ToolRegistry, log: HarnessLog
) -> PrefetchResult | None:
    """`/chat/stream`'s additions to the goal, once per run: the mode's
    prefetch (its blocks and records into the citation state, its context into
    the constraints). Returns the prefetch, which grounds a hand-off (ADR-RUN-03 D1)."""
    import logging

    from app.agents.agent_loop.retrieval_ledger import emit_prefetch_retrieval_context
    from app.agents.chat_modes.setup import apply_prefetch, start_prefetch

    legacy_log = logging.getLogger("app.harness.assembly.chat")
    started = start_prefetch(
        turn.setup,
        context,
        turn.policy,
        retrieval_service=turn.retrieval_service,
        graph_provider=turn.graph_provider,
        is_multimodal_llm=turn.is_multimodal,
        log=legacy_log,
    )
    prefetched = await started if started is not None else None
    apply_prefetch(context, goal, prefetched, registry=registry, log=legacy_log)
    # Into the presenter's sink: journaled with the pre-run commit (inert unless the run opted in).
    await emit_prefetch_retrieval_context(context, scheduled=started is not None, result=prefetched)
    log.info(
        "prerun.chat",
        mode=turn.policy.name,
        prefetch=started is not None,
        prefetched=prefetched is not None and not prefetched.is_empty,
        blocks=len(prefetched.final_results) if prefetched is not None else 0,
        images=len(prefetched.collected_images) if prefetched is not None else 0,
        attachments=len(turn.setup.attachments.virtual_record_ids),
    )
    return prefetched


__all__ = ["ChatTurn", "build_chat_environment", "shape_chat_goal"]
