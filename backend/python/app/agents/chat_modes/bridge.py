"""`run_chat_stream()`: the agent-loop entry point for ALL THREE
`/chat/stream` modes (internal_search, web_search, agent) — the chat-mode
counterpart to `app.agents.agent_loop.stream_bridge.run_agent_loop_stream()`,
deliberately mirroring its signature and SSE wire format so the two are
recognizably parallel and `chatbot.py`'s route handler can call this the
same way `agent.py::chat_stream()` calls the other.

No FastAPI types below this point: `query_info`/`user_info` are plain
dicts (same convention `run_agent_loop_stream()` uses) so this package
never imports from `app.api.routes` — the route layer resolves `request.
state.user`/builds these dicts, this layer only ever sees plain data.

Every chat mode runs the agent loop's existing "quick" mode (flat ReAct,
`skip_intent=True`, zero routing/intent LLM calls — see `policy.py`'s
module docstring for why no `ToolPolicy`/`modes.py` changes were needed).
Modes differ only in:

- which tools are available this turn (`has_knowledge` / `web_search_
  config` / `has_sql_knowledge` / `has_slack_knowledge` forced on the
  `ChatState` dict before it becomes an `AgentContext`);
- whether upfront retrieval (`prefetch.prefetch_retrieval()`) runs before
  the agent's first turn, run CONCURRENTLY with `PipesHubAgentFactory.
  create()` via `asyncio.gather` so simple internal-search queries pay
  retrieval latency once, in parallel with tool/hook wiring, and then
  answer in a single turn with zero tool calls;
- a short mode-specific addendum folded into `context.instructions`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.answer_streamer import TerminalAnswerStreamer
from app.agents.agent_loop.clarification import emit_pre_run_clarification
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.error_classification import classify_error
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.hooks import CitationCollector, ensure_fetch_full_record_available
from app.agents.agent_loop.respond import AnswerFinalizer
from app.agents.agent_loop.stream_bridge import (
    QueueEventSink,
    _cancel_orphaned_agent_tasks,
    _heartbeat,
    _pre_stream_error_frame,
    sse_queue_maxsize,
)
from app.agents.chat_modes.attachments import resolve_attachments
from app.agents.chat_modes.custom_instructions import (
    resolve_custom_instructions as _resolve_custom_instructions,
)
from app.agents.chat_modes.policy import (
    INTERNAL_SEARCH_POLICY,
    ChatModePolicy,
    resolve_chat_mode_policy,
)
from app.agents.chat_modes.prefetch import prefetch_retrieval
from app.config.constants.service import config_node_constants
from app.utils.chat_helpers import CitationRefMapper, get_message_content
from app.utils.streaming import create_sse_event, handle_simple_mode

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

_DONE = object()

# Mode-specific addendum folded into `AgentContext.instructions` (rendered
# under "## Agent Instructions" by `PipesHubPromptBuilder` — see that
# module's docstring). Mirrors the intent of chatbot.py's pre-migration
# `get_model_config_for_mode()["system_prompt"]` per-mode framing, kept
# short since the agent loop's own system prompt already covers tool usage,
# citation rules, and the "Internal Knowledge First" section in detail.
_MODE_INSTRUCTIONS: dict[str, str] = {
    "internal_search": (
        "Answer ONLY from the organization's internal knowledge base / provided context and "
        "attachments. Do not use your own training knowledge to answer factual questions. If the "
        "answer is not present in the retrieved context, say so explicitly rather than guessing.\n\n"
        "The context you are given contains the most relevant BLOCKS from each document — not "
        "the full documents. For questions that can be answered by locating the right passage "
        "(a date, a name, a number, a status), the blocks are sufficient. For questions that "
        "need whole-document content — a summary, what the risks or key points are, a review, "
        "a comparison, anything covering all of something — a handful of blocks cannot support "
        "the answer. When a candidate list appears below the context blocks showing how much of "
        "each record you hold (\"you have N of M blocks\"), call `knowledgegraph__fetch_record` "
        "with those record IDs before answering."
    ),
    "web_search": (
        "Use web search for this conversation. Do not use internal company knowledge or tools -- "
        "answer using publicly available information found via web_search/fetch_url."
    ),
    "agent": (
        "Decide per-query whether internal knowledge search, web search, or both are needed to "
        "answer well -- you have both available. Prefer internal knowledge for company-specific "
        "questions and web search for current events/public information; use both when a query "
        "needs each."
    ),
}


def _with_mode_instructions(base_instructions: str | None, policy: ChatModePolicy) -> str:
    addendum = _MODE_INSTRUCTIONS.get(policy.system_prompt_key, "")
    if not addendum:
        return base_instructions or ""
    if base_instructions and base_instructions.strip():
        return f"{base_instructions.strip()}\n\n{addendum}"
    return addendum


async def _resolve_web_search_config(config_service: Any, logger: logging.Logger) -> dict[str, Any] | None:
    """Mirrors `_generate_web_search_stream()`'s provider lookup: the org's
    default web-search provider, falling back to DuckDuckGo when none is
    explicitly configured.

    The Node.js config API (`cm_controller.ts::getWebSearchProviders`) never
    persists a DuckDuckGo entry into the stored config -- it treats
    DuckDuckGo as the implicit default whenever no stored provider carries
    `isDefault: true` (including a brand-new org with an empty/absent
    `providers` list). Reading the raw stored config here without the same
    fallback would silently disable `web_search`/`fetch_url` for every org
    that hasn't explicitly configured a paid provider, even though the UI
    tells the user DuckDuckGo is already active (see the identical fallback
    in `agent.py::_resolve_default_web_search_config`)."""
    try:
        web_search_config = await config_service.get_config(
            config_node_constants.WEB_SEARCH.value, default={}, use_cache=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_chat_stream: failed to load web search config: %s", exc)
        return None
    providers = (web_search_config or {}).get("providers") or []
    default_provider = next((p for p in providers if p.get("isDefault")), None)
    if not default_provider:
        return {"provider": "duckduckgo", "configuration": {}}
    return {
        "provider": default_provider.get("provider"),
        "configuration": default_provider.get("configuration", {}),
    }


async def _prefetch_available_connectors(
    chat_state: dict[str, Any],
    *,
    graph_provider: Any,
    user_id: str,
    org_id: str,
    log: logging.Logger,
) -> None:
    """Fetch user-visible connector metadata once and cache it in ``chat_state``.

    Stored as ``chat_state["available_connectors"]`` — a list of
    ``{id, name, type}`` dicts. ``ConnectorCatalog.build`` reads this key and
    skips the DB round-trip.  Failures are silently swallowed so the chat
    mode degrades gracefully (tools still work; catalog falls back to the
    lazy path inside each tool call instead).
    """
    try:
        user = await graph_provider.get_user_by_user_id(user_id=user_id)
        user_key = (user.get("_key") or user.get("id")) if user else None
        if not user_key:
            return
        options = await graph_provider.get_knowledge_hub_filter_options(
            user_key=user_key, org_id=org_id,
        )
        apps = (options or {}).get("apps") or []
        chat_state["available_connectors"] = [
            {"id": a.get("id", ""), "name": a.get("name", ""), "type": a.get("type", "")}
            for a in apps
            if isinstance(a, dict) and a.get("id")
        ]
        log.debug(
            "run_chat_stream: prefetched %d connector(s) for catalog",
            len(chat_state["available_connectors"]),
        )
    except Exception as exc:
        log.debug("run_chat_stream: connector prefetch failed (non-fatal): %s", exc)


def _apply_policy_to_chat_state(
    chat_state: dict[str, Any], policy: ChatModePolicy, web_search_config: dict[str, Any] | None,
) -> None:
    """Forces the tool-availability flags `PipesHubToolLoader`/`AgentContext.
    from_chat_state` read, per `policy.py`'s module docstring. Chat modes
    have no per-agent "knowledge scope" the way agents do, so SQL/Slack
    tool availability mirrors connector PRESENCE (`has_sql_connector`/
    `has_slack_connector`, already resolved by the caller), not a
    knowledge-attachment concept that doesn't exist here."""
    chat_state["has_knowledge"] = policy.has_knowledge
    chat_state["has_sql_knowledge"] = policy.has_knowledge and bool(chat_state.get("has_sql_connector"))
    chat_state["has_slack_knowledge"] = policy.has_knowledge and bool(chat_state.get("has_slack_connector"))
    chat_state["web_search_config"] = web_search_config if policy.include_web_search else None
    chat_state["chat_mode"] = policy.name


async def _build_no_tools_messages(
    *, query: str, ai_models_config: dict[str, Any], context_text: str, user_data: str,
    is_multimodal_llm: bool, ref_mapper: CitationRefMapper,
) -> list[dict[str, Any]]:
    """Minimal message set for the Ollama/no-tool-calls degradation path --
    a plain system+user pair, no agent-loop machinery. Reuses `get_message_
    content()` (`app.utils.chat_helpers`, a shared utility, not chatbot.py)
    for the exact same `<record>`-block + citation-ID formatting every other
    path uses, so citations still normalize identically."""
    system_prompt = (
        "You are an assistant. Answer queries in a professional, enterprise-appropriate format. "
        "You MUST ONLY answer based on the provided internal knowledge base context. Do NOT use "
        "your own training knowledge. If the answer is not present in the provided context, "
        "respond with: 'This information is not available in the internal knowledge base.'"
    )
    # Only `internal_search` ever reaches this builder (see the mode guard in
    # `_run_no_tools_degradation`), but resolve through the same shared
    # helper as the tool-calling path so the org-prompt lookup has one
    # source of truth.
    custom_prompt = _resolve_custom_instructions(ai_models_config, INTERNAL_SEARCH_POLICY)
    if custom_prompt:
        system_prompt += f"\n\n{custom_prompt}"
    content, ref_mapper = get_message_content(
        [], {}, user_data, query, ref_mapper=ref_mapper,
    )
    if context_text:
        content = [{"type": "text", "text": context_text}, *content]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]


async def _run_no_tools_degradation(
    *, query_info: dict[str, Any], user_info: dict[str, Any], llm: "BaseChatModel",
    policy: ChatModePolicy, log: logging.Logger, retrieval_service: Any, graph_provider: Any,
    config_service: Any, ai_models_config: dict[str, Any], is_multimodal_llm: bool,
    context_length: int,
) -> AsyncGenerator[str, None]:
    """`supports_tool_calls=False` (Ollama) path. `internal_search` degrades
    gracefully to a single no-tools turn seeded with forced (`force=True`)
    prefetch context -- functionally identical to today's chatbot `mode=
    "no_tools"` behavior. `web_search`/`agent` need tool calling to do
    anything at all, so they fail fast with a clear error instead of
    silently answering from training data alone."""
    if policy.name != "internal_search":
        yield create_sse_event("error", {
            "error": f"This model does not support tool calling, which {policy.name.replace('_', ' ')} mode requires.",
            "type": "unsupported_model_mode",
        })
        return

    from app.modules.transformers.blob_storage import BlobStorage

    org_id = user_info.get("orgId", "")
    user_id = user_info.get("userId", "")
    blob_store = BlobStorage(logger=log, config_service=config_service, graph_provider=graph_provider)

    yield create_sse_event("status", {"status": "searching", "message": "Searching knowledge base..."})
    prefetch = await prefetch_retrieval(
        query=query_info.get("query", ""), org_id=org_id, user_id=user_id,
        retrieval_service=retrieval_service, graph_provider=graph_provider, blob_store=blob_store,
        filters=query_info.get("filters"), limit=query_info.get("limit"),
        is_multimodal_llm=is_multimodal_llm, previous_conversations=query_info.get("previous_conversations"),
        logger=log, force=True,
    )
    ref_mapper = prefetch.citation_ref_mapper if prefetch else CitationRefMapper()
    context_text = prefetch.formatted_context if prefetch else ""

    messages = await _build_no_tools_messages(
        query=query_info.get("query", ""), ai_models_config=ai_models_config, context_text=context_text,
        user_data="", is_multimodal_llm=is_multimodal_llm, ref_mapper=ref_mapper,
    )

    try:
        async for event in handle_simple_mode(
            llm=llm,
            messages=messages,
            final_results=prefetch.final_results if prefetch else [],
            records=[],
            logger=log,
            target_words_per_chunk=1,
            virtual_record_id_to_result=prefetch.virtual_record_id_to_result if prefetch else {},
            ref_to_url=ref_mapper.ref_to_url if ref_mapper else None,
        ):
            yield create_sse_event(event["event"], event["data"])
    except Exception as exc:
        log.error("run_chat_stream: no-tools degradation stream failed: %s", exc, exc_info=True)
        yield create_sse_event("error", {"error": f"Stream error: {exc}"})


async def run_chat_stream(  # noqa: PLR0913 - mirrors run_agent_loop_stream's call-site symmetry
    query_info: dict[str, Any],
    user_info: dict[str, Any],
    llm: "BaseChatModel",
    policy: ChatModePolicy | None,
    log: logging.Logger,
    retrieval_service: Any,
    graph_provider: Any,
    reranker_service: Any,
    config_service: Any,
    org_info: dict[str, Any] | None = None,
    model_name: str | None = None,
    model_key: str | None = None,
    is_multimodal_llm: bool = False,
    context_length: int = 128000,
    ai_models_config: dict[str, Any] | None = None,
    supports_tool_calls: bool = True,
    protocol: str = "legacy",
    client_name: str | None = None,
) -> "AsyncGenerator[str, None]":
    """Entry point `chatbot.py::askAIStream()` calls for every `/chat/stream`
    request, regardless of mode. See module docstring."""
    from app.modules.agents.qna.chat_state import build_initial_state
    from app.utils.execute_query import has_sql_connector_configured
    from app.utils.fetch_slack_thread import has_slack_connector_configured
    from app.modules.transformers.blob_storage import BlobStorage

    policy = policy or resolve_chat_mode_policy(query_info.get("chatMode"))
    ai_models_config = ai_models_config or {}

    if not supports_tool_calls:
        async for event in _run_no_tools_degradation(
            query_info=query_info, user_info=user_info, llm=llm, policy=policy, log=log,
            retrieval_service=retrieval_service, graph_provider=graph_provider,
            config_service=config_service, ai_models_config=ai_models_config,
            is_multimodal_llm=is_multimodal_llm, context_length=context_length,
        ):
            yield event
        return

    try:
        has_sql_connector = await has_sql_connector_configured(
            graph_provider, user_info["userId"], user_info["orgId"]
        )
        has_slack_connector = await has_slack_connector_configured(
            graph_provider, user_info["userId"], user_info["orgId"]
        )
        web_search_config = (
            await _resolve_web_search_config(config_service, log) if policy.include_web_search else None
        )

        blob_store = BlobStorage(logger=log, config_service=config_service, graph_provider=graph_provider)
        ref_mapper = CitationRefMapper()
        resolved_attachments = await resolve_attachments(
            query_info.get("attachments"), blob_store=blob_store, org_id=user_info.get("orgId", ""),
            ref_mapper=ref_mapper, logger=log,
        )
        filters = dict(query_info.get("filters") or {})
        if resolved_attachments.virtual_record_ids:
            # Widened, never narrowed: an attachment scoped search must not
            # accidentally EXCLUDE the rest of the org's knowledge the user
            # didn't explicitly filter to.
            existing_kb = list(filters.get("kb") or [])
            filters["kb"] = list({*existing_kb, *resolved_attachments.virtual_record_ids})
        query_info = {**query_info, "filters": filters, "chatMode": policy.loop_chat_mode}

        chat_state = build_initial_state(
            query_info, user_info, llm, log, retrieval_service, graph_provider,
            reranker_service, config_service, model_name or "", model_key or "", org_info,
            "react", has_sql_connector=has_sql_connector, is_multimodal_llm=is_multimodal_llm,
            has_slack_connector=has_slack_connector, client_name=client_name,
        )
        _apply_policy_to_chat_state(chat_state, policy, web_search_config)
        chat_state["instructions"] = _with_mode_instructions(chat_state.get("instructions"), policy)
        chat_state["custom_instructions"] = _resolve_custom_instructions(ai_models_config, policy)
        chat_state["citation_ref_mapper"] = ref_mapper

        # For chat modes with knowledge enabled, pre-fetch user-visible connectors
        # so the catalog (ConnectorCatalog.build) and capability_summary can use
        # them without an extra round-trip per tool call. The query is cheap
        # (one indexed graph traversal), and the result is cached in chat_state.
        if policy.has_knowledge:
            await _prefetch_available_connectors(
                chat_state, graph_provider=graph_provider,
                user_id=user_info.get("userId", ""), org_id=user_info.get("orgId", ""),
                log=log,
            )
    except Exception as exc:
        log.error("run_chat_stream: failed to build initial state: %s", exc, exc_info=True)
        error_code, user_message = classify_error(str(exc))
        yield _pre_stream_error_frame(protocol, user_message, error_code)
        return

    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=sse_queue_maxsize())
    event_sink = QueueEventSink(queue)
    # Store event_sink and protocol in chat_state so ArtifactManager tools
    # (promote_artifact, etc.) can emit live SSE events without needing
    # direct access to AgentContext — chat_state IS tool_state in AgentContext.
    chat_state["event_sink"] = event_sink
    chat_state["sse_protocol"] = protocol
    context = AgentContext.from_chat_state(chat_state, event_sink=event_sink, protocol=protocol)

    async def _produce() -> None:
        agent: Any = None
        try:
            factory = PipesHubAgentFactory()
            prefetch_task = (
                asyncio.ensure_future(
                    prefetch_retrieval(
                        query=query_info.get("query", ""), org_id=context.org_id, user_id=context.user_id,
                        retrieval_service=retrieval_service, graph_provider=graph_provider,
                        blob_store=blob_store, filters=query_info.get("filters"), limit=query_info.get("limit"),
                        is_multimodal_llm=is_multimodal_llm,
                        previous_conversations=query_info.get("previous_conversations"), logger=log,
                        # Same mapper `web_search`/`fetch_url` (via
                        # `tool_loader.py`) already closed over inside
                        # `factory.create()` below -- see
                        # `prefetch_retrieval`'s `ref_mapper` docstring.
                        ref_mapper=ref_mapper,
                    )
                )
                if policy.prefetch_retrieval else None
            )

            agent, _runtime, goal, clarifying_questions = await factory.create(
                context, llm, policy.loop_chat_mode,
                query=query_info.get("query", ""), model_name=model_name or "",
            )

            prefetch_result = await prefetch_task if prefetch_task is not None else None
            if prefetch_result is not None and not prefetch_result.is_empty:
                context.tool_state["final_results"] = [
                    *context.tool_state.get("final_results", []), *prefetch_result.final_results,
                ]
                context.tool_state["virtual_record_id_to_result"] = {
                    **context.tool_state.get("virtual_record_id_to_result", {}),
                    **prefetch_result.virtual_record_id_to_result,
                }
                context.tool_state["tool_records"] = [
                    *context.tool_state.get("tool_records", []), *prefetch_result.tool_records,
                ]
                # No `citation_ref_mapper` reassignment here: `prefetch_result.
                # citation_ref_mapper` IS `ref_mapper` (passed in above), the
                # same instance `context.tool_state["citation_ref_mapper"]`
                # already holds and web_search/fetch_url already closed over.

                # Compute coverage and candidate list for the prefetched results.
                # The retrieval TOOL never runs in prefetch mode, so this is the
                # only place where we can produce the "you have N of M blocks"
                # coverage counts the model needs to judge whether a full fetch
                # would add anything. Without this, the model is handed document
                # blocks with no basis for knowing how much is missing.
                from app.modules.agents.record_escalation import (
                    analyze_coverage,
                    build_candidates,
                    render_candidate_table,
                )
                all_final = context.tool_state["final_results"]
                vr_map = context.tool_state["virtual_record_id_to_result"]
                coverage = analyze_coverage(all_final, vr_map)

                # Build records in prefetch relevance order (final_results order,
                # de-duped on first appearance), matching retrieval.py's approach.
                seen_rids: set[str] = set()
                records_in_order: list[dict] = []
                for entry in all_final:
                    vrid = entry.get("virtual_record_id")
                    if not vrid:
                        continue
                    rec = vr_map.get(vrid)
                    if not rec:
                        continue
                    rid = rec.get("id")
                    if rid and rid not in seen_rids:
                        seen_rids.add(rid)
                        records_in_order.append(rec)

                plan = build_candidates(
                    coverage=coverage,
                    records_in_relevance_order=records_in_order,
                    already_fetched_ids=set(),
                )
                context.tool_state["fetch_coverage"] = coverage
                context.tool_state["fetch_plan"] = plan

                candidate_suffix = (
                    render_candidate_table(
                        plan,
                        needs_whole_document=context.needs_whole_document,
                    )
                    if plan.has_candidates
                    else ""
                )
                log.info(
                    "prefetch: needs_whole_document=%s | candidates=%s",
                    context.needs_whole_document,
                    [c.record_id for c in plan.candidates],
                )

                context_with_candidates = prefetch_result.formatted_context + candidate_suffix
                goal.constraints.append(
                    "Relevant internal knowledge base context retrieved for this query "
                    f"(cite using the Citation IDs shown, e.g. [source](ref1)):\n{context_with_candidates}"
                )
                ensure_fetch_full_record_available(
                    context, registry=_runtime.tool_registry,
                )
            if resolved_attachments.context_text:
                goal.constraints.append(resolved_attachments.context_text)

            if clarifying_questions:
                await emit_pre_run_clarification(
                    context, goal.description, clarifying_questions, event_sink=context.event_sink,
                )
            else:
                collector = CitationCollector(context)
                streamer = TerminalAnswerStreamer(context, collector, context.event_sink)
                async for event in agent.stream(goal):
                    await streamer.on_event(event)
                result = agent.last_stream_result

                finalizer = AnswerFinalizer(context, collector)
                await finalizer.run(
                    agent_success=result.success, agent_error=result.error,
                    agent_output=result.output, event_sink=context.event_sink,
                    streamed_answer=streamer.streamed_answer, reasoning_turns=streamer.reasoning_turns,
                )
        except Exception as exc:
            log.error("run_chat_stream: run failed: %s", exc, exc_info=True)
            error_code, user_message = classify_error(str(exc))
            for evt in context.formatter.error(context, message=user_message, code=error_code):
                await context.event_sink.write(evt)
        finally:
            await _cancel_orphaned_agent_tasks(agent)
            if context.sandbox_manager is not None:
                try:
                    await context.sandbox_manager.destroy_all()
                except Exception:
                    log.warning("run_chat_stream: sandbox cleanup failed", exc_info=True)
            await event_sink.flush()
            await queue.put(_DONE)

    producer = asyncio.create_task(_produce())
    heartbeat = asyncio.create_task(_heartbeat(queue)) if protocol == "agui" else None
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"
    finally:
        if heartbeat and not heartbeat.done():
            heartbeat.cancel()
        if not producer.done():
            producer.cancel()
        tasks = [producer]
        if heartbeat:
            tasks.append(heartbeat)
        await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["run_chat_stream"]
