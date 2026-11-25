"""What a built-in assistant turn (`/chat/stream`, no agent) is built from.

Every chat mode runs the agent loop's "quick" mode (flat ReAct,
`skip_intent=True`, zero routing/intent LLM calls — see `policy.py`'s
module docstring for why no `ToolPolicy`/`modes.py` changes were needed).
Modes differ only in:

- which tools are available this turn (`has_knowledge` / `web_search_
  config` / `has_sql_knowledge` / `has_slack_knowledge` forced on the
  `ChatState` dict before it becomes an `AgentContext`);
- whether upfront retrieval (`prefetch.prefetch_retrieval()`) runs before
  the agent's first turn, so simple internal-search queries pay retrieval
  latency once and then answer in a single turn with zero tool calls;
- a short mode-specific addendum folded into `context.instructions`.

Both routes build a turn from these functions: the legacy stream
(`stream_bridge.run_chat_stream`, which overlaps the prefetch with
`PipesHubAgentFactory.create()`) and a durable run of the built-in
assistant (`app.harness.assembly`, where the prefetch is a journaled
pre-run step).

No FastAPI types here: `query_info`/`user_info` are plain dicts.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.scope import ATTACHMENT_RECORD_IDS_KEY
from app.agents.agent_loop.answer_rules import (
    READ_RECORDS_IN_FULL_STATE_KEY,
    answer_composition_rules_enabled,
    thorough_retrieval_enabled,
)
from app.agents.agent_loop.hooks import ensure_fetch_full_record_available
from app.agents.agent_loop.period_summary import timeline_replaces_prefetch
from app.agents.chat_modes.attachments import ResolvedAttachments, resolve_attachments
from app.agents.chat_modes.custom_instructions import resolve_custom_instructions
from app.agents.chat_modes.internal_search_prompt import internal_search_instructions
from app.agents.chat_modes.prefetch import (
    PrefetchResult,
    filter_notice_constraint,
    prefetch_retrieval,
    search_failure_constraint,
    search_incomplete_constraint,
)
from app.core.org_context import current_org_id, spawn_in_request
from app.harness.knowledge.caller import caller_user_id_of
from app.modules.demo_data.chat import (
    demo_exclusions_for_run,
    exclude_from_query,
    exclude_from_state,
    note_org_real_data,
)
from app.modules.retrieval.provenance import note_retrieved_by
from app.utils.chat_helpers import CitationRefMapper, ImageBudget
from app.utils.connector_instances import fetch_user_connector_instances
from app.utils.error_class import log_failure
from app.utils.image_admission import admission_from_state
from app.utils.logging_fields import log_event
from app.utils.user_lookup import get_caller_user
from app.web.config import resolve_web_search

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import Goal
    from app.agents.agent_loop.context import AgentContext
    from app.agents.chat_modes.policy import ChatModePolicy

# Mode-specific addendum folded into `AgentContext.instructions` (rendered
# under "## Agent Instructions" by `PipesHubPromptBuilder` — see that
# module's docstring). Mirrors the intent of chatbot.py's pre-migration
# `get_model_config_for_mode()["system_prompt"]` per-mode framing, kept
# short since the agent loop's own system prompt already covers tool usage,
# citation rules, and the "Internal Knowledge First" section in detail.
# `internal_search` is built by `internal_search_instructions`, whose text
# depends on the answer-rule switches.
MODE_INSTRUCTIONS: dict[str, str] = {
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


def with_mode_instructions(
    base_instructions: str | None,
    policy: ChatModePolicy,
    *,
    composition: bool = False,
    thorough: bool = False,
    entity_tools: bool = False,
) -> str:
    if policy.system_prompt_key == "internal_search":
        addendum = internal_search_instructions(
            composition=composition, thorough=thorough, entity_tools=entity_tools,
        )
    else:
        addendum = MODE_INSTRUCTIONS.get(policy.system_prompt_key, "")
    if not addendum:
        return base_instructions or ""
    if base_instructions and base_instructions.strip():
        return f"{base_instructions.strip()}\n\n{addendum}"
    return addendum


def apply_mode_instructions(chat_state: dict[str, Any], policy: ChatModePolicy) -> None:
    """Fold the mode text into `chat_state["instructions"]`, and tell the
    prompt builder whether its fetch policy must ask for full reads to match.

    Reads each switch once, so the mode text and the fetch policy can never
    disagree within a request. Entity tools are granted exactly when an
    entity store is wired (`factory._initial_entity_tool_grant`)."""
    internal_search = policy.system_prompt_key == "internal_search"
    thorough = internal_search and thorough_retrieval_enabled()
    chat_state["instructions"] = with_mode_instructions(
        chat_state.get("instructions"), policy,
        composition=internal_search and answer_composition_rules_enabled(),
        thorough=thorough,
        entity_tools=bool(chat_state.get("entity_vector_store")),
    )
    chat_state[READ_RECORDS_IN_FULL_STATE_KEY] = thorough


async def _none() -> None:
    """Placeholder awaitable so a conditional lookup can still take a slot in
    the `asyncio.gather` below without branching the result unpacking."""


async def fetch_available_connectors(
    *,
    graph_provider: Any,  # noqa: ANN401
    user_key: str | None,
    user_id: str,
    org_id: str,
    log: logging.Logger,
) -> list[dict[str, str]] | None:
    """User-visible connector metadata as ``{id, name, type}`` dicts, for
    ``chat_state["available_connectors"]``.

    ``user_key`` comes from the user document the route already loaded
    (`chatbot.py`); only a caller that didn't resolve one pays for the lookup
    here. Failures return ``None`` so the chat mode degrades gracefully —
    the catalog falls back to its lazy path inside each tool call.
    """
    try:
        if not user_key:
            user = await get_caller_user(graph_provider, user_id)
            user_key = (user.get("_key") or user.get("id")) if user else None
        if not user_key:
            return None
        options = await graph_provider.get_knowledge_hub_filter_options(
            user_key=user_key, org_id=org_id,
        )
        apps = (options or {}).get("apps") or []
        connectors = [
            {"id": a.get("id", ""), "name": a.get("name", ""), "type": a.get("type", "")}
            for a in apps
            if isinstance(a, dict) and a.get("id")
        ]
        log.debug(
            "run_chat_stream: prefetched %d connector(s) for catalog", len(connectors),
        )
        return connectors
    except Exception as exc:
        log_event(log, "connector_prefetch.failed", level=logging.DEBUG, error_type=type(exc).__name__)
        return None


def apply_policy_to_chat_state(
    chat_state: dict[str, Any], policy: ChatModePolicy, web_search_config: dict[str, Any] | None,
    sql_connector_ids: frozenset[str] = frozenset(),
) -> None:
    """Forces the tool-availability flags `PipesHubToolLoader`/`AgentContext.
    from_chat_state` read, per `policy.py`'s module docstring. Chat modes
    have no per-agent "knowledge scope" the way agents do, so SQL/Slack/code
    tool availability mirrors connector PRESENCE (`has_sql_connector`/
    `has_slack_connector`/`has_code_connector`, already resolved by the
    caller), not a knowledge-attachment concept that doesn't exist here. For
    the same reason the SQL tool's connector allowlist is the user's own SQL
    connector instances (`sql_connector_ids`)."""
    chat_state["has_knowledge"] = policy.has_knowledge
    chat_state["has_sql_knowledge"] = policy.has_knowledge and bool(chat_state.get("has_sql_connector"))
    chat_state["allowed_sql_connector_ids"] = (
        sql_connector_ids if chat_state["has_sql_knowledge"] else frozenset()
    )
    chat_state["has_slack_knowledge"] = policy.has_knowledge and bool(chat_state.get("has_slack_connector"))
    chat_state["has_code_knowledge"] = policy.has_knowledge and bool(chat_state.get("has_code_connector"))
    chat_state["web_search_config"] = web_search_config if policy.include_web_search else None
    chat_state["deep_research"] = policy.deep_research and chat_state["web_search_config"] is not None
    chat_state["web_search_unset"] = policy.include_web_search and web_search_config is None
    chat_state["chat_mode"] = policy.name


def enrich_chat_identity(
    user_info: dict[str, Any],
    org_id: str | None,
    user_doc: object,
    org_doc: object,
    user_context_flag: object,
    log: logging.Logger,
) -> dict[str, Any] | None:
    """Adds the user's names and key (from their graph document) to
    `user_info`, applies the org's user-context setting, and returns the org
    info the prompt names. Each input may be the exception its lookup raised:
    enrichment is best-effort."""
    org_info: dict[str, Any] | None = None
    if isinstance(user_doc, BaseException):
        log.debug("Failed to load user doc for prompt enrichment", exc_info=user_doc)
    elif user_doc and isinstance(user_doc, dict):
        for field in ("fullName", "firstName", "lastName", "displayName"):
            if user_doc.get(field):
                user_info[field] = user_doc[field]
        # Reused by `fetch_available_connectors` so the same user lookup is
        # not repeated further down the request.
        user_key = user_doc.get("_key") or user_doc.get("id")
        if user_key:
            user_info["userKey"] = user_key
    if isinstance(org_doc, BaseException):
        log.debug("Failed to load org doc for prompt enrichment", exc_info=org_doc)
    elif org_doc and isinstance(org_doc, dict):
        raw_account_type = str(org_doc.get("accountType", "")).lower()
        org_info = {
            "orgId": org_id,
            "accountType": raw_account_type if raw_account_type in ("enterprise", "individual") else "",
            "name": org_doc.get("name") or "",
        }

    if isinstance(user_context_flag, BaseException):
        log.debug(
            "Failed to read ENABLE_USER_CONTEXT; defaulting to enabled",
            exc_info=user_context_flag,
        )
        user_context_enabled = True
    else:
        user_context_enabled = bool(user_context_flag)
    if not user_context_enabled:
        user_info["sendUserInfo"] = False
    return org_info


class TurnScopeRefused(Exception):
    """The turn's scope cannot be searched (main #4255): ``code`` and ``message`` are what the routes answer;
    ``error_class`` is what the turn's `agent_turn.end` line records."""

    def __init__(self, code: str, message: str, *, error_class: str = "forbidden") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.error_class = error_class


async def _turn_filters(
    query_info: dict[str, Any],
    user_info: dict[str, Any],
    org_id: str,
    policy: ChatModePolicy,
    graph_provider: Any,  # noqa: ANN401
    resolved_attachments: ResolvedAttachments,
    log: logging.Logger,
) -> dict[str, Any]:
    """The turn's filters with the server-set keys in place (``prepare_turn_scope``); a selection that cannot be
    searched, or a turn that can only search what the user cannot read, is refused before the model runs."""
    from app.modules.retrieval.selection_scope import (
        SelectionNotReadyError,
        SelectionTooLargeError,
        prepare_turn_scope,
        selection_error,
    )

    project_sources = query_info.get("allowedFilters")
    try:
        filters = await prepare_turn_scope(
            graph_provider, user_info.get("userId", ""), org_id,
            query_info.get("filters"),
            project_sources=project_sources if isinstance(project_sources, dict) else None,
            connection_sources=query_info.get("connectionLimit"),
        )
    except (SelectionTooLargeError, SelectionNotReadyError) as exc:
        raise TurnScopeRefused(*selection_error(exc), error_class="bad_request") from exc
    if policy.has_knowledge and not policy.include_web_search and not resolved_attachments.virtual_record_ids:
        await refuse_unreadable_scope(graph_provider, user_info, org_id, filters, log)
    return filters


async def refuse_unreadable_scope(
    graph_provider: Any,  # noqa: ANN401
    user_info: dict[str, Any],
    org_id: str,
    filters: dict[str, Any],
    log: logging.Logger,
) -> None:
    """Main's bridge rule (#4255) for a turn that can only search: raises ``TurnScopeRefused`` when the user can
    read nothing its filters admit. The caller decides "can only search" (knowledge on, no web, no attachment)."""
    from app.modules.retrieval.selection_scope import unreadable_scope_error

    # Without this, such a turn retries its search tools until the run's time budget is spent.
    try:
        unreadable = await unreadable_scope_error(
            graph_provider, user_info.get("userId", ""), org_id, filters,
        )
    except Exception as exc:
        # The turn still runs.
        log_failure(log, "turn_scope.check_failed", exc)
        return
    if unreadable is not None:
        raise TurnScopeRefused(*unreadable)


@dataclass
class ChatSetup:
    """A built-in assistant turn's state, before it becomes an `AgentContext`."""

    chat_state: dict[str, Any]
    query_info: dict[str, Any]
    attachments: ResolvedAttachments
    ref_mapper: CitationRefMapper
    blob_store: Any


async def prepare_chat_state(
    query_info: dict[str, Any],
    user_info: dict[str, Any],
    llm: Any,  # noqa: ANN401 - a LangChain chat model
    policy: ChatModePolicy,
    log: logging.Logger,
    *,
    retrieval_service: Any,  # noqa: ANN401
    graph_provider: Any,  # noqa: ANN401
    reranker_service: Any,  # noqa: ANN401
    config_service: Any,  # noqa: ANN401
    org_info: dict[str, Any] | None,
    model_name: str | None,
    model_key: str | None,
    is_multimodal_llm: bool,
    system_prompts_config: dict[str, Any],
    client_name: str | None,
    entity_vector_store: Any = None,  # noqa: ANN401
    org_id: str | None = None,
) -> ChatSetup:
    """The `ChatState` of one turn in `policy`'s mode: connector flags, the
    web-search provider, the attachments (which widen the KB filter), the
    user's visible connectors, demo exclusions, and the mode's instructions.

    ``entity_vector_store`` (optional) backs the knowledge-graph
    ``search_entities``/``find_records_by_entity`` tools; with ``None`` they
    are not granted (see ``factory._initial_entity_tool_grant``).

    ``org_id``: the org a durable run's claim names; unset, the org the request's entry point bound."""
    from app.modules.agents.qna.chat_state import build_initial_state
    from app.modules.code_graph.connectors import connector_instances_have_code
    from app.modules.transformers.blob_storage import BlobStorage
    from app.utils.execute_query import (
        connector_instances_have_sql,
        sql_connector_instance_ids,
    )
    from app.utils.fetch_slack_thread import connector_instances_have_slack

    # The turn acts for the org its entry point bound (the chat route's token) or its run's claim, never a payload's.
    org_id = org_id or current_org_id()
    blob_store = BlobStorage(logger=log, config_service=config_service, graph_provider=graph_provider)
    ref_mapper = CitationRefMapper()

    # Everything needed to build the initial state is independent, and all
    # of it sits before the first streamed byte -- run it as one wave. The
    # SQL and Slack checks used to issue the SAME `get_user_connector_
    # instances` query twice; they now share one result.
    connector_instances, web_search_config, resolved_attachments, available_connectors, demo_excluded = (
        await asyncio.gather(
            fetch_user_connector_instances(
                graph_provider, user_info["userId"], org_id, log,
            ),
            resolve_web_search(config_service, log, use_cache=True) if policy.include_web_search else _none(),
            resolve_attachments(
                query_info.get("attachments"),
                org_id=org_id, logger=log,
                user_id=user_info.get("userId") or "",
                graph_provider=graph_provider,
                is_service_account=bool(user_info.get("isServiceAccount")),
            ),
            # Pre-fetch user-visible connectors so the catalog
            # (ConnectorCatalog.build) and capability_summary can use them
            # without an extra round-trip per tool call.
            fetch_available_connectors(
                graph_provider=graph_provider,
                user_key=user_info.get("userKey"),
                user_id=user_info.get("userId", ""),
                org_id=org_id,
                log=log,
            ) if policy.has_knowledge else _none(),
            demo_exclusions_for_run(graph_provider, config_service, user_info, log),
        )
    )
    has_sql_connector = connector_instances_have_sql(connector_instances)
    has_slack_connector = connector_instances_have_slack(connector_instances)
    has_code_connector = connector_instances_have_code(connector_instances)
    filters = await _turn_filters(query_info, user_info, org_id, policy, graph_provider, resolved_attachments, log)
    if resolved_attachments.virtual_record_ids:
        # Widened, never narrowed: an attachment scoped search must not
        # accidentally EXCLUDE the rest of the org's knowledge the user
        # didn't explicitly filter to.
        existing_kb = list(filters.get("kb") or [])
        filters["kb"] = list({*existing_kb, *resolved_attachments.virtual_record_ids})
    query_info = {**query_info, "filters": filters, "chatMode": policy.loop_chat_mode}
    query_info = exclude_from_query(query_info, demo_excluded)

    chat_state = build_initial_state(
        query_info, user_info, llm, log, retrieval_service, graph_provider,
        reranker_service, config_service, model_name or "", model_key or "", org_info,
        "react", has_sql_connector=has_sql_connector, is_multimodal_llm=is_multimodal_llm,
        has_slack_connector=has_slack_connector,
        has_code_connector=has_code_connector, client_name=client_name,
        entity_vector_store=entity_vector_store,
    )
    apply_policy_to_chat_state(
        chat_state, policy, web_search_config,
        sql_connector_ids=sql_connector_instance_ids(connector_instances, org_id),
    )
    apply_mode_instructions(chat_state, policy)
    chat_state["custom_instructions"] = resolve_custom_instructions(system_prompts_config, policy)
    chat_state["citation_ref_mapper"] = ref_mapper
    chat_state[ATTACHMENT_RECORD_IDS_KEY] = resolved_attachments.virtual_record_ids
    # Read by `ConnectorCatalog.build`, which skips its own DB round-trip
    # when this key is present.
    if available_connectors is not None:
        chat_state["available_connectors"] = available_connectors
    exclude_from_state(chat_state, demo_excluded)
    await note_org_real_data(chat_state, graph_provider, org_id, log)
    return ChatSetup(chat_state, query_info, resolved_attachments, ref_mapper, blob_store)


def start_prefetch(
    setup: ChatSetup,
    context: AgentContext,
    policy: ChatModePolicy,
    *,
    retrieval_service: Any,  # noqa: ANN401
    graph_provider: Any,  # noqa: ANN401
    is_multimodal_llm: bool,
    log: logging.Logger,
) -> asyncio.Future[PrefetchResult | None] | None:
    """The mode's upfront retrieval, started at once (`None` when the mode has none)."""
    query_info = setup.query_info
    # A question over a span of time is answered period by period by the timeline search, so an
    # upfront search for it would only crowd the context (main #4296).
    if not policy.prefetch_retrieval or timeline_replaces_prefetch(query_info.get("query")):
        return None
    # Shared with `retrieval.py`/`citations.py`'s tool-result image
    # collection so the 50-image cap is enforced across the whole
    # turn regardless of whether an image came from prefetch or a
    # later search/fetch tool call -- see `ImageBudget`.
    image_budget = context.tool_state.setdefault("image_budget", ImageBudget())
    return spawn_in_request(
        prefetch_retrieval(
            query=query_info.get("query", ""), org_id=context.org_id, user_id=context.user_id,
            retrieval_service=retrieval_service, graph_provider=graph_provider,
            blob_store=setup.blob_store, filters=query_info.get("filters"), limit=query_info.get("limit"),
            is_multimodal_llm=is_multimodal_llm,
            previous_conversations=query_info.get("previous_conversations"), logger=log,
            # Same mapper `web_search`/`fetch_url` (via `tool_loader.py`)
            # already close over inside `factory.create()` -- see
            # `prefetch_retrieval`'s `ref_mapper` docstring.
            ref_mapper=setup.ref_mapper,
            image_budget=image_budget,
            image_admission=admission_from_state(context.tool_state),
            caller_user_id=caller_user_id_of(context.user_info),
            # Set by `apply_mode_instructions` before this runs: under the
            # full-read policy the fetch tool takes no `query`.
            read_in_full=bool(context.tool_state.get(READ_RECORDS_IN_FULL_STATE_KEY)),
        )
    )


def apply_prefetch(
    context: AgentContext,
    goal: Goal,
    prefetch_result: PrefetchResult | None,
    *,
    registry: Any,  # noqa: ANN401 - the run's ToolRegistry
    log: logging.Logger,
) -> None:
    """Folds a prefetch into the turn: its blocks and records into the tool
    state the citations are built from, its images into the attachment
    images, the context (with the coverage note and candidate list) into the
    goal's constraints, and `fetch_full_record` into the registry. A search
    that was filtered away, failed or came back incomplete says so in the goal."""
    from app.modules.agents.qna.chat_state import remember_shown_results

    if prefetch_result is not None and prefetch_result.filter_notice:
        goal.constraints.append(filter_notice_constraint(prefetch_result.filter_notice))
    elif prefetch_result is not None and prefetch_result.failure_notice:
        goal.constraints.append(search_failure_constraint(prefetch_result.failure_notice, can_search=True))
    if prefetch_result is None or prefetch_result.is_empty:
        return
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
    remember_shown_results(
        context.tool_state,
        prefetch_result.ranked_results,
        prefetch_result.virtual_record_id_to_result,
    )
    note_retrieved_by(context.tool_state, "prefetch", [
        (vrid, record.get("id"), record.get("record_name"))
        for vrid, record in prefetch_result.virtual_record_id_to_result.items()
        if isinstance(record, dict)
    ])
    # No `citation_ref_mapper` reassignment here: `prefetch_result.
    # citation_ref_mapper` IS the turn's mapper (passed to the prefetch),
    # the same instance `context.tool_state["citation_ref_mapper"]`
    # already holds and web_search/fetch_url already closed over.

    if prefetch_result.collected_images:
        # Prefetch produces plain text folded into
        # `goal.constraints`, not a tool result -- so its images
        # ride the SAME `shape_image_injection` PRE_MODEL hook
        # that already delivers user-attachment images, rather
        # than a multipart `ToolMessage`. Extend (not overwrite):
        # `factory.create()` already ran and may have
        # populated this from the user's own attachments.
        context.attachment_image_blocks = [
            *context.attachment_image_blocks,
            *prefetch_result.collected_images,
        ]

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
        render_coverage_note,
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

    candidate_suffix = ""
    coverage_note = ""
    read_in_full = bool(context.tool_state.get(READ_RECORDS_IN_FULL_STATE_KEY))
    if plan.has_candidates:
        candidate_suffix = render_candidate_table(
            plan, needs_whole_document=context.needs_whole_document,
            read_in_full=read_in_full,
        )
        # Leads the constraint: the table lands after the whole
        # prefetched context, far enough down that the model may
        # already be answering from the blocks by the time it
        # reads how little of each record it holds.
        coverage_note = render_coverage_note(
            plan, needs_whole_document=context.needs_whole_document,
            read_in_full=read_in_full,
        )
    log.debug(
        "prefetch: needs_whole_document=%s | candidates=%d",
        context.needs_whole_document, len(plan.candidates),
    )

    incomplete_note = (
        search_incomplete_constraint(prefetch_result.incomplete_notice, can_search=True)
        if prefetch_result.incomplete_notice else ""
    )
    context_with_candidates = (
        incomplete_note + coverage_note + prefetch_result.formatted_context + candidate_suffix
    )
    goal.constraints.append(
        "Relevant internal knowledge base context retrieved for this query "
        f"(cite using the Citation IDs shown, e.g. [source](ref1)):\n{context_with_candidates}"
    )
    ensure_fetch_full_record_available(context, registry=registry)


__all__ = [
    "MODE_INSTRUCTIONS",
    "TurnScopeRefused",
    "ChatSetup",
    "apply_mode_instructions",
    "apply_policy_to_chat_state",
    "apply_prefetch",
    "enrich_chat_identity",
    "fetch_available_connectors",
    "prepare_chat_state",
    "refuse_unreadable_scope",
    "start_prefetch",
    "with_mode_instructions",
]
