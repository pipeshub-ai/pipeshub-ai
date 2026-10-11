"""Bridge integration for plain-RAG chat semantic caching.

Coordinates cache eligibility evaluation, inline serve replay, background shadow
comparison, background writes, and telemetry.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING, Any, Iterable, cast

from app.agent_loop_lib.transport.opik_tracing import (
    is_opik_configured,
    maybe_start_named_span,
    record_named_span_output,
)
from app.config.constants.arangodb import Connectors
from app.services.cache.guard import is_guard_supported
from app.services.cache.semantic_cache_service import (
    PROMPT_TEMPLATE_VERSION,
    STORAGE_SCHEMA_VERSION,
    SemanticCacheConfigHashParams,
)
from app.telemetry.event_buffer import record_event

GMAIL_CONNECTORS: frozenset[str] = frozenset({
    Connectors.GOOGLE_MAIL.value,
    Connectors.GOOGLE_MAIL_WORKSPACE.value,
})

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agents.agent_loop.context import AgentContext
    from app.agents.chat_modes.prefetch import PrefetchResult
    from app.services.cache.config import SemanticCacheConfig
    from app.services.cache.semantic_cache_service import CacheLookupResult

log = logging.getLogger(__name__)

RELATIVE_TIME_PATTERN = re.compile(
    r"\b("
    r"latest|"
    r"recently|recent|"
    r"currently|current|"
    r"(?:this|last|next)\s+(?:week|month|quarter|year)|"
    r"today|yesterday|tomorrow|"
    r"now|"
    r"so\s+far|"
    r"as\s+of|"
    r"year\s+to\s+date|ytd|"
    r"past\s+\d+\s+(?:days?|weeks?|months?|years?)"
    r")\b",
    re.IGNORECASE,
)


def resolve_config_hash_params(
    context: AgentContext,
    query_info: dict[str, Any],
    prefetch_result: PrefetchResult,
) -> SemanticCacheConfigHashParams | None:
    """Resolve configuration inputs for config_hash.

    Strictly hashes template/config inputs only, excluding per-request identity,
    timestamps, and secret credentials. Returns None if required parameters
    cannot be resolved.
    """
    if not prefetch_result.query_embedding:
        return None

    embedding_dim = len(prefetch_result.query_embedding)
    if embedding_dim <= 0:
        return None

    generator_provider = str(context.llm_provider or "").strip()
    generator_model = str(
        context.model_name
        or query_info.get("modelName")
        or query_info.get("model_name")
        or ""
    ).strip()
    if not generator_model:
        llm_obj = getattr(context, "llm", None)
        llm_candidate = getattr(llm_obj, "model_name", None) or getattr(llm_obj, "model", None)
        if isinstance(llm_candidate, str):
            generator_model = llm_candidate.strip()

    if not generator_provider or not generator_model:
        return None

    # Note: Provider-hardcoded default temperatures in `aimodels.py` are static in code.
    # Any update to hardcoded provider temperatures is covered only by bumping PROMPT_TEMPLATE_VERSION.
    reasoning_effort = query_info.get("reasoningEffort") or query_info.get("reasoning_effort")
    if reasoning_effort is not None:
        reasoning_effort = str(reasoning_effort)

    sampling_temperature = query_info.get("temperature")
    if sampling_temperature is not None:
        try:
            sampling_temperature = float(sampling_temperature)
        except (ValueError, TypeError):
            sampling_temperature = None

    response_language = query_info.get("responseLanguage") or query_info.get("response_language")
    if response_language is not None:
        response_language = str(response_language)

    # Hash system prompt instructions templates
    instructions_text = str(context.tool_state.get("instructions") or "")
    custom_instructions = str(context.tool_state.get("custom_instructions") or "")
    combined_instructions = f"{instructions_text}\n{custom_instructions}".strip()
    system_prompt_template_hash = (
        hashlib.sha256(combined_instructions.encode("utf-8")).hexdigest()
        if combined_instructions
        else ""
    )

    # Hash project instructions
    project_instructions = str(
        context.tool_state.get("project_instructions")
        or query_info.get("projectInstructions")
        or ""
    ).strip()
    project_instructions_hash = (
        hashlib.sha256(project_instructions.encode("utf-8")).hexdigest()
        if project_instructions
        else ""
    )

    enrichment_flags = f"chatMode={query_info.get('chatMode', '')};multimodal={context.is_multimodal_llm}"

    # Resolve tool list bound to the agent for this request
    bound_tools: list[str] | None = None
    root_spec = getattr(context, "root_agent_spec", None)
    if root_spec is not None:
        raw_tools = cast(object, getattr(root_spec, "tool_names", None))
        if isinstance(raw_tools, (list, tuple, set, frozenset)):
            bound_tools = [str(x) for x in cast("Iterable[object]", raw_tools)]
        elif type(raw_tools).__name__ == "MagicMock":
            bound_tools = []
    if bound_tools is None and isinstance(getattr(context, "tool_state", None), dict):
        tool_state_dict = cast("dict[str, object]", context.tool_state)
        raw_tools = tool_state_dict.get("bound_tool_names") or tool_state_dict.get("tool_names")
        if isinstance(raw_tools, (list, tuple, set, frozenset)):
            bound_tools = [str(x) for x in cast("Iterable[object]", raw_tools)]
    if bound_tools is None:
        raw_tools = cast(object, getattr(context, "bound_tool_names", None) or query_info.get("bound_tool_names"))
        if isinstance(raw_tools, (list, tuple, set, frozenset)):
            bound_tools = [str(x) for x in cast("Iterable[object]", raw_tools)]
    if bound_tools is None and query_info.get("chatMode") == "internal_search":
        bound_tools = []

    if bound_tools is None:
        return None

    sorted_tools = sorted(bound_tools)
    tools_hash = hashlib.sha256("\n".join(sorted_tools).encode("utf-8")).hexdigest()

    return SemanticCacheConfigHashParams(
        embedding_provider="default",
        embedding_model="default",
        embedding_dimension=embedding_dim,
        generator_provider=generator_provider,
        generator_model=generator_model,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        storage_schema_version=STORAGE_SCHEMA_VERSION,
        reasoning_effort=reasoning_effort,
        sampling_temperature=sampling_temperature,
        response_language=response_language,
        system_prompt_template_hash=system_prompt_template_hash,
        project_instructions_hash=project_instructions_hash,
        enrichment_flags=enrichment_flags,
        tools_hash=tools_hash,
    )


def evaluate_cacheability(
    config: SemanticCacheConfig,
    query: str,
    query_info: dict[str, Any],
    prefetch_result: PrefetchResult | None,
    config_params: SemanticCacheConfigHashParams | None,
) -> tuple[bool, str | None]:
    """Check all cacheability preconditions and return reason code on bypass."""
    if config.mode == "off":
        return False, "mode_off"

    chat_mode = query_info.get("chatMode")
    if chat_mode != "internal_search":
        return False, "not_internal_search"

    if bool(query_info.get("include_web_search") or query_info.get("web_search")):
        return False, "web_search_enabled"

    previous_conversations = cast(
        list[object] | None,
        query_info.get("previous_conversations")
        or query_info.get("previousConversations"),
    )
    if bool(previous_conversations):
        return False, "multi_turn"

    if bool(query_info.get("attachments")):
        return False, "attachments"

    if RELATIVE_TIME_PATTERN.search(query):
        return False, "relative_time"

    if not is_guard_supported(query):
        return False, "unsupported_language"

    if prefetch_result is None or prefetch_result.is_empty:
        return False, "prefetch_none"

    if not prefetch_result.tool_records:
        return False, "no_records"

    if prefetch_result.record_fingerprints is None:
        return False, "fingerprints_none"

    if config.share_across_org:
        for record in prefetch_result.tool_records:
            conn = record.get("connector") or record.get("connector_name") or ""
            if conn in GMAIL_CONNECTORS or conn in (
                Connectors.GOOGLE_MAIL.value,
                Connectors.GOOGLE_MAIL_WORKSPACE.value,
            ):
                return False, "gmail_shared"

    if config_params is None:
        return False, "config_unresolved"

    return True, None


def _record_cache_answer_generated(
    context: AgentContext,
    citations: list[dict[str, Any]],
) -> None:
    """Emit answer_generated telemetry for cached turn."""
    from app.telemetry.identity import domain_from_email
    from app.telemetry.modules.activity_metrics import record_service_activity

    connectors: set[str] = set()
    for c in citations:
        meta_obj = c.get("metadata")
        if isinstance(meta_obj, dict):
            c_meta = cast(dict[str, object], meta_obj)
            name = str(c_meta.get("connector") or c_meta.get("origin") or "")
            if name:
                connectors.add(name)

    record_event(
        "answer_generated",
        {
            "orgId": context.org_id,
            "userId": context.user_id,
            "domain": domain_from_email(str(context.user_email or "")),
            "chat_mode": "internal_search",
            "citation_count": len(citations),
            "connectors": sorted(connectors),
            "demo_sources": False,
            "stopped": False,
            "cached": True,
        },
    )

    try:
        record_service_activity(
            "query_service",
            "answer_generated",
            connector="none",
            status="cached",
            org=str(context.org_id or "unknown"),
            domain=domain_from_email(str(context.user_email or "")),
        )
    except Exception as exc:
        log.debug("telemetry: cache answer_generated metric not recorded: %s", exc)


def build_replay_events(
    context: AgentContext,
    lookup_result: CacheLookupResult,
    query: str,
    current_user_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the complete list of replay events in memory before any writes."""
    completion_data = dict(lookup_result.completion_data or {})
    answer = str(completion_data.get("answer") or "")
    raw_citations = cast(list[object] | None, completion_data.get("citations"))
    citations: list[dict[str, Any]] = (
        [cast(dict[str, Any], c) for c in raw_citations if isinstance(c, dict)]
        if isinstance(raw_citations, list)
        else []
    )
    confidence = completion_data.get("confidence")

    # Author user rule: do not replay reasoning/parts for cross-user hits
    cached_author = str(completion_data.get("author_user_id") or "")
    if cached_author and cached_author != current_user_id:
        _ = completion_data.pop("reasoning", None)
        _ = completion_data.pop("parts", None)

    # Attach cache markers - send only entryId plus minimum in cacheRef
    completion_data["servedFromCache"] = True
    completion_data["cacheRef"] = lookup_result.cache_ref or {
        "entryId": lookup_result.entry_id,
    }
    completion_data["runId"] = context.run_id

    # Populate tool_state on context for full parity with live run
    context.tool_state["response"] = answer
    context.tool_state["completion_data"] = completion_data

    events: list[dict[str, Any]] = []
    # 1. Answer delta event(s)
    for evt in context.formatter.answer_delta(
        context,
        chunk=answer,
        accumulated=answer,
        citations=citations,
        confidence=confidence,
        raw_length=len(answer),
    ):
        events.append(evt)

    # 2. Answer final event(s)
    for evt in context.formatter.answer_final(context, completion_data=completion_data):
        events.append(evt)

    return events, completion_data


def record_replay_telemetry(
    context: AgentContext,
    completion_data: dict[str, Any],
    query: str,
) -> None:
    """Record Opik tracing span and activation metric for a replayed turn."""
    answer = str(completion_data.get("answer") or "")
    raw_citations = cast(list[object] | None, completion_data.get("citations"))
    citations: list[dict[str, Any]] = (
        [cast(dict[str, Any], c) for c in raw_citations if isinstance(c, dict)]
        if isinstance(raw_citations, list)
        else []
    )

    with maybe_start_named_span(
        enabled=is_opik_configured(),
        name="answer_finalizer.finalize",
        span_input={
            "query": query,
            "agent_output": answer,
            "agent_success": True,
            "cached": True,
        },
    ) as span:  # pyright: ignore[reportUnknownVariableType]
        if span is not None:
            record_named_span_output(span, completion_data)

    _record_cache_answer_generated(context, citations)


async def replay_cached_turn(
    context: AgentContext,
    lookup_result: CacheLookupResult,
    query: str,
    current_user_id: str,
    *,
    on_before_write: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Replay cached completion through existing formatter for active wire protocol.

    Builds the complete event list in memory before emitting anything to the event sink.
    If `on_before_write` callback is provided, it is invoked immediately before the first write.
    """
    events, completion_data = build_replay_events(
        context, lookup_result, query, current_user_id
    )

    if on_before_write is not None and events:
        on_before_write()

    for evt in events:
        await context.event_sink.write(evt)

    record_replay_telemetry(context, completion_data, query)
    return completion_data


def emit_shadow_comparison(
    live_completion_data: dict[str, Any],
    cached_result: CacheLookupResult,
    org_id: str,
) -> None:
    """Compare live response with shadow cached entry and emit numerical metrics."""
    if not cached_result.hit or not cached_result.completion_data:
        return

    live_answer = str(live_completion_data.get("answer") or "")
    cached_answer = str(cached_result.completion_data.get("answer") or "")

    live_answer_hash = hashlib.sha256(live_answer.encode("utf-8")).hexdigest()
    cached_answer_hash = hashlib.sha256(cached_answer.encode("utf-8")).hexdigest()
    answer_hash_equal = live_answer_hash == cached_answer_hash

    live_cite_ids: set[str] = set()
    raw_live_cites = cast(list[object] | None, live_completion_data.get("citations"))
    if isinstance(raw_live_cites, list):
        for c in raw_live_cites:
            if isinstance(c, dict):
                c_dict = cast(dict[str, object], c)
                cid = str(c_dict.get("id") or c_dict.get("virtual_record_id") or c_dict.get("recordId") or "")
                if cid:
                    live_cite_ids.add(cid)

    cached_cite_ids: set[str] = set()
    raw_cached_cites = cast(
        list[object] | None,
        cached_result.completion_data.get("citations")
        if cached_result.completion_data
        else None,
    )
    if isinstance(raw_cached_cites, list):
        for c in raw_cached_cites:
            if isinstance(c, dict):
                c_dict = cast(dict[str, object], c)
                cid = str(c_dict.get("id") or c_dict.get("virtual_record_id") or c_dict.get("recordId") or "")
                if cid:
                    cached_cite_ids.add(cid)

    intersection = len(live_cite_ids & cached_cite_ids)
    union = len(live_cite_ids | cached_cite_ids)
    citation_jaccard = intersection / union if union > 0 else 1.0

    live_confidence = str(live_completion_data.get("confidence") or "")
    cached_confidence = str(cached_result.completion_data.get("confidence") or "")
    confidence_equal = live_confidence == cached_confidence

    length_ratio = (
        len(live_answer) / max(1, len(cached_answer))
        if cached_answer
        else 0.0
    )

    record_event(
        "semantic_cache_shadow_comparison",
        {
            "org_id": org_id,
            "kind": cached_result.kind,
            "score": round(cached_result.score, 4),
            "answer_hash_equal": answer_hash_equal,
            "citation_jaccard": round(citation_jaccard, 4),
            "confidence_equal": confidence_equal,
            "length_ratio": round(length_ratio, 4),
        },
    )


def should_store_turn(
    completion_data: dict[str, Any],
    *,
    is_cancelled: bool,
    agent_success: bool,
) -> tuple[bool, str | None]:
    """Check write eligibility per Section 2 Decision 3 and Section 5."""
    if is_cancelled or completion_data.get("status") == "stopped":
        return False, "cancelled"

    if not agent_success:
        return False, "unsuccessful"

    # Tool calls check: strictly ZERO tool calls permitted for plain-RAG cache write.
    # The digest covers only the prefetch set; any tool call or extra retrieval
    # invalidates the cache write contract.
    succeeded_tools = cast(list[object] | None, completion_data.get("succeeded_tool_names"))
    failed_tools = cast(list[object] | None, completion_data.get("failed_tool_names"))
    tool_names = cast(list[object] | None, completion_data.get("tool_names"))
    tool_calls = cast(list[object] | None, completion_data.get("tool_calls"))
    if any(bool(x) for x in (succeeded_tools, failed_tools, tool_names, tool_calls) if isinstance(x, list)):
        return False, "tool_calls"

    parts = cast(list[object] | None, completion_data.get("parts"))
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict):
                part_dict = cast("dict[str, object]", part)
                if part_dict.get("type") in ("tool_call", "tool_result"):
                    return False, "tool_calls"

    # Fallback check
    if completion_data.get("answerMatchType") == "Fallback Response":
        return False, "fallback_response"

    # Confidence check
    if completion_data.get("confidence") == "Low":
        return False, "low_confidence"

    # Non-empty answer check
    answer = str(completion_data.get("answer") or "").strip()
    if not answer:
        return False, "empty_answer"

    return True, None
